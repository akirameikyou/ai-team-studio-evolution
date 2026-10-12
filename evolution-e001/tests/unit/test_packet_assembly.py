"""E001-F1/U3-3: packet assembly (application/packet_assembly.py).

Acceptance conditions B1-B10 of U3-3_MINIMAL_IMPLEMENTATION_SPEC_001.md and the exception tests of
U3-3_IMPLEMENTATION_INSTRUCTIONS_001.md (review copy). Every test runs on the E001-D scenario in a
temporary store; selections come from U3-2 select_candidates.
"""
import hashlib
from unittest import mock

from application import packet_assembly as pa
from application.candidate_selection import EVALUATED, INCOMPLETE, select_candidates
from application.execution_context import ContextReferenceError
from application.packet_assembly import ASSEMBLED, NOT_ASSEMBLED, assemble_packet
from domain.protocol.packet import decode_packet
from infrastructure.storage import artifact_store, event_log, record_store
from infrastructure.storage.event_log import SYSTEM_ACTOR_ID
from tests.integration.support import SECRETS, StudioTestCase
from tests.persistence.support import snapshot, tamper


class AssemblyTestCase(StudioTestCase):
    def setUp(self):
        super().setUp()
        self.p1 = self.w.run_p001()

    def selection(self, edge_id, run_id=None):
        return select_candidates(self.studio, run_id or self.p1['run'], edge_id)

    def assert_not_assembled(self, result, code):
        self.assertEqual(result.status, NOT_ASSEMBLED)
        self.assertEqual(result.problems[0][0], code)
        self.assertEqual((result.header, result.artifact_ids, result.packet_bytes, result.packet_sha256), (None, (), b'', None))

    def no_reads(self):
        """Fail the test if the store is read."""
        return mock.patch.object(self.studio.artifacts, 'read', side_effect=AssertionError('store was read'))


class AcceptanceTests(AssemblyTestCase):
    def test_b1_case_edge_packet_decodes_to_the_initial_inputs_in_manifest_order(self):
        sel = self.selection('case-a')
        self.assertEqual(sel.status, EVALUATED)
        res = assemble_packet(self.studio, sel)
        self.assertEqual((res.status, res.problems), (ASSEMBLED, ()))
        pkt = decode_packet(res.packet_bytes)
        h = pkt.header
        self.assertEqual((h.run_id, h.edge_id, h.from_node_id, h.to_node_id, h.protocol_id, h.protocol_version),
                         (self.p1['run'], 'case-a', SYSTEM_ACTOR_ID, self.w.obs['a'], 'PROTO-P001', '1.0'))
        self.assertEqual(res.header, h)
        self.assertEqual([a.artifact_id for a in pkt.artifacts], [self.w.case_input, self.w.source_artifact])
        self.assertEqual(res.artifact_ids, (self.w.case_input, self.w.source_artifact))
        for a in pkt.artifacts:
            self.assertEqual(a.content, self.studio.artifacts.read(a.artifact_id).content)
        self.assertEqual([a.kind for a in pkt.artifacts], ['CASE_INPUT', 'SOURCE_CONTENT'])
        self.assertEqual(res.packet_sha256, hashlib.sha256(res.packet_bytes).hexdigest())

    def test_b2_a_packet_carries_no_peer_secret(self):
        res = assemble_packet(self.studio, self.selection('case-a'))
        self.assertEqual(res.status, ASSEMBLED)
        for x in 'bc':
            self.assertNotIn(SECRETS[x].encode(), res.packet_bytes)

    def test_b3_p002_b_to_c_carries_only_b_answer(self):
        p2 = self.w.run_p002()
        res = assemble_packet(self.studio, self.selection('b-to-c', p2['run']))
        self.assertEqual(res.status, ASSEMBLED)
        pkt = decode_packet(res.packet_bytes)
        self.assertEqual([a.artifact_id for a in pkt.artifacts], [p2['raw']['b']])
        self.assertNotIn(SECRETS['a'].encode(), res.packet_bytes)
        self.assertIn(SECRETS['b'].encode(), res.packet_bytes)

    def test_b4_incomplete_selection_is_not_assembled_and_reads_nothing(self):
        tamper(self.studio.artifacts.artifacts_dir / self.w.case_input / 'payload.bin', b'changed question')
        sel = self.selection('case-a')
        self.assertEqual(sel.status, INCOMPLETE)
        with self.no_reads():
            res = assemble_packet(self.studio, sel)
        self.assert_not_assembled(res, 'SELECTION_INCOMPLETE')
        self.assertEqual(res.problems[1:], sel.problems)

    def test_b5_unresolved_is_not_assembled_and_reads_nothing(self):
        second = self.w.respond(self.p1['run'], self.p1['transfers']['b'], self.w.obs['b'], 'B again')
        sel = self.selection('syn-b')
        self.assertEqual(len(sel.unresolved), 2)
        with self.no_reads():
            res = assemble_packet(self.studio, sel)
        self.assert_not_assembled(res, 'UNRESOLVED_PRESENT')
        detail = ' '.join(m for _, m in res.problems)
        for aid in (self.p1['raw']['b'], second):
            self.assertIn(aid, detail)
        self.assertIn(self.w.obs['b'], detail)

    def test_b6_nothing_accepted_is_not_assembled(self):
        run = self.w.start(self.w.trial('P001'))  # started, no transfer yet
        sel = self.selection('syn-a', run)
        self.assertEqual((sel.status, sel.accepted, sel.unresolved), (EVALUATED, (), ()))
        with self.no_reads():
            res = assemble_packet(self.studio, sel)
        self.assert_not_assembled(res, 'NO_ACCEPTED_CANDIDATES')

    def test_b7_artifact_changed_after_selection(self):
        sel = self.selection('case-a')
        self.assertEqual(sel.status, EVALUATED)
        tamper(self.studio.artifacts.artifacts_dir / self.w.case_input / 'payload.bin', b'changed question')
        res = assemble_packet(self.studio, sel)
        self.assert_not_assembled(res, 'ARTIFACT_UNREADABLE')
        self.assertIn(self.w.case_input, res.problems[0][1])

    def test_b8_assembly_writes_nothing(self):
        sel = self.selection('case-a')
        root = self.studio.artifacts.root
        before_files = snapshot(root)
        before_events = self.studio.events.read_all()
        before_transfers = self.studio.records.list('transfer')
        trap = AssertionError('a write API was called')
        with mock.patch.object(self.studio.artifacts, 'create', side_effect=trap), \
                mock.patch.object(self.studio.records, 'put', side_effect=trap), \
                mock.patch.object(self.studio.events, 'append', side_effect=trap), \
                mock.patch.object(self.studio, 'record_transfer', side_effect=trap), \
                mock.patch.object(artifact_store, 'create_exclusive', side_effect=trap), \
                mock.patch.object(record_store, 'create_exclusive', side_effect=trap), \
                mock.patch.object(event_log, 'create_exclusive', side_effect=trap):
            res = assemble_packet(self.studio, sel)
        self.assertEqual(res.status, ASSEMBLED)
        self.assertEqual(snapshot(root), before_files)
        self.assertEqual(self.studio.events.read_all(), before_events)
        self.assertEqual(self.studio.records.list('transfer'), before_transfers)

    def test_b9_same_input_same_packet(self):
        sel = self.selection('case-a')
        first, second = assemble_packet(self.studio, sel), assemble_packet(self.studio, sel)
        self.assertEqual(first, second)
        self.assertEqual(first.status, ASSEMBLED)

    def test_b10_order_of_accepted_does_not_matter(self):
        sel = self.selection('case-a')
        normal = assemble_packet(self.studio, sel)
        reversed_ = assemble_packet(self.studio, sel._replace(accepted=tuple(reversed(sel.accepted))))
        self.assertEqual(sel.accepted, (self.w.case_input, self.w.source_artifact))
        self.assertEqual((reversed_.packet_bytes, reversed_.artifact_ids), (normal.packet_bytes, normal.artifact_ids))


class ExceptionTests(AssemblyTestCase):
    def test_manifest_lookup_failure_is_record_unreadable(self):
        sel = self.selection('case-a')
        with mock.patch.object(self.studio, 'manifest_of', side_effect=ContextReferenceError('run gone')):
            res = assemble_packet(self.studio, sel)
        self.assert_not_assembled(res, 'RECORD_UNREADABLE')

    def test_manifest_integrity_failure_is_record_unreadable(self):
        sel = self.selection('case-a')
        with mock.patch.object(self.studio, 'manifest_of', side_effect=record_store.RecordIntegrityError('records/x', [])):
            res = assemble_packet(self.studio, sel)
        self.assert_not_assembled(res, 'RECORD_UNREADABLE')

    def test_duplicate_accepted_id_is_a_packet_format_error(self):
        sel = self.selection('case-a')
        res = assemble_packet(self.studio, sel._replace(accepted=(self.w.case_input, self.w.case_input)))
        self.assert_not_assembled(res, 'PACKET_FORMAT_ERROR')

    def test_os_error_while_reading_an_artifact(self):
        err = OSError(13, 'denied', 'C:/store/artifacts/x/payload.bin')
        sel = self.selection('case-a')
        with mock.patch.object(self.studio.artifacts, 'read', side_effect=err):
            res = assemble_packet(self.studio, sel)
        self.assert_not_assembled(res, 'ARTIFACT_UNREADABLE')
        self.assertIn('payload.bin', res.problems[0][1])

    def test_os_error_while_reading_the_manifest(self):
        sel = self.selection('case-a')
        with mock.patch.object(self.studio, 'manifest_of', side_effect=OSError(13, 'denied', 'records/runManifest/m.json')):
            res = assemble_packet(self.studio, sel)
        self.assert_not_assembled(res, 'RECORD_UNREADABLE')
        self.assertIn('m.json', res.problems[0][1])

    def test_os_error_outside_the_reading_calls_propagates(self):
        sel = self.selection('case-a')
        with mock.patch.object(pa, 'encode_packet', side_effect=OSError('not a read')):
            with self.assertRaises(OSError):
                assemble_packet(self.studio, sel)

    def test_unexpected_error_while_reading_propagates(self):
        sel = self.selection('case-a')
        with mock.patch.object(self.studio.artifacts, 'read', side_effect=TypeError('bug')):
            with self.assertRaises(TypeError):
                assemble_packet(self.studio, sel)

    def test_unexpected_error_in_the_manifest_lookup_propagates(self):
        sel = self.selection('case-a')
        with mock.patch.object(self.studio, 'manifest_of', side_effect=KeyError('runManifestId')):
            with self.assertRaises(KeyError):
                assemble_packet(self.studio, sel)

    def test_p4_comes_before_p5(self):
        sel = self.selection('case-a')
        tamper(self.studio.artifacts.artifacts_dir / self.w.case_input / 'payload.bin', b'changed')
        with mock.patch.object(self.studio, 'manifest_of', side_effect=AssertionError('manifest read before artifacts')):
            res = assemble_packet(self.studio, sel)
        self.assert_not_assembled(res, 'ARTIFACT_UNREADABLE')
