"""E001-F1/U4 (minimal, plan A): packet persistence (application/packet_persistence.py).

Cases T1-T14 of U4_MINIMAL_IMPLEMENTATION_SPEC_002.md (review copy). Every test runs on the E001-D
scenario in a temporary store. Failures are injected with mock; nothing simulates a real power loss.
"""
import json
from unittest import mock

from application import packet_persistence as pp
from application.packet_assembly import assemble_packet
from application.packet_persistence import ALREADY_RECORDED, NOT_RECORDED, PARTIAL_STATE, RECORDED, persist_packet
from domain.protocol.packet import decode_packet
from infrastructure.storage import artifact_store
from tests.integration.support import StudioTestCase
from tests.persistence.support import snapshot, tamper


class PersistTestCase(StudioTestCase):
    def setUp(self):
        super().setUp()
        self.run = self.w.start(self.w.trial('P001'))  # started, no transfer yet

    def packets(self, run_id=None):
        out = []
        for aid in self.studio.artifacts.list_ids():
            try:
                meta = self.studio.artifacts.read(aid).metadata()
            except artifact_store.StoreError:
                continue
            if meta['kind'] == 'PACKET' and meta['provenance']['runId'] == (run_id or self.run):
                out.append(aid)
        return out

    def transfers(self, edge_id, run_id=None):
        return [t for t in self.studio.records.records('transfer') if (t['runId'], t['edgeId']) == (run_id or self.run, edge_id)]

    def codes(self, res):
        return [c for c, _ in res.reasons]

    def answered_twice_by_b(self):
        tid, _ = self.w.send(self.run, 'case-b', [self.w.case_input, self.w.source_artifact])
        first = self.w.respond(self.run, tid, self.w.obs['b'], 'B once')
        second = self.w.respond(self.run, tid, self.w.obs['b'], 'B twice')
        return first, second


class NormalAndPrecheckTests(PersistTestCase):
    def test_t1_records_packet_and_transfer(self):
        res = persist_packet(self.studio, self.run, 'case-a')
        self.assertEqual((res.status, res.reasons, res.wrote, res.needs_human), (RECORDED, (), True, False))
        self.assertTrue(res.store_check_complete)
        self.assertEqual(self.packets(), [res.packet_artifact_id])
        [t] = self.transfers('case-a')
        self.assertEqual((t['transferId'], t['packetArtifactId'], t['status'], t['isolationAssurance']),
                         (res.transfer_id, res.packet_artifact_id, 'PACKET_ASSEMBLED', 'PACKET_CONTROLLED'))
        art = self.studio.artifacts.read(res.packet_artifact_id)
        meta = art.metadata()
        self.assertEqual((meta['provenance']['sourceKind'], meta['provenance']['receivedVia'], meta['parentArtifactIds']),
                         ('SYSTEM', 'SYSTEM_GENERATED', []))
        self.assertEqual(art.content_hash, res.packet_sha256)
        self.assertEqual(t['packetHash'], res.packet_sha256)
        self.assertEqual([a.artifact_id for a in decode_packet(art.content).artifacts], t['artifactIds'])
        self.assertEqual(t['artifactIds'], [self.w.case_input, self.w.source_artifact])
        created = [e for e in self.studio.events.read_all()[0] if e['eventType'] == 'TRANSFER_CREATED' and e['targetId'] == res.transfer_id]
        self.assertEqual(len(created), 1)

    def test_t2_unresolved_stops_before_writing(self):
        self.answered_twice_by_b()
        before = snapshot(self.studio.artifacts.root)
        res = persist_packet(self.studio, self.run, 'syn-b')
        self.assertEqual(res.status, NOT_RECORDED)
        self.assertIn('UNRESOLVED_PRESENT', self.codes(res))
        self.assertFalse(res.wrote)
        self.assertEqual(snapshot(self.studio.artifacts.root), before)

    def test_t3_nothing_accepted_stops_before_writing(self):
        before = snapshot(self.studio.artifacts.root)
        res = persist_packet(self.studio, self.run, 'syn-a')
        self.assertEqual((res.status, self.codes(res)), (NOT_RECORDED, ['NO_ACCEPTED_CANDIDATES']))
        self.assertEqual(snapshot(self.studio.artifacts.root), before)

    def test_t4_packet_and_selection_disagree(self):
        real = assemble_packet
        variants = {
            'artifact order': lambda a: a._replace(artifact_ids=tuple(reversed(a.artifact_ids))),
            'header': lambda a: a._replace(packet_bytes=a.packet_bytes.replace(b'edge: case-a', b'edge: case-b', 1)),
            'hash': lambda a: a._replace(packet_sha256='0' * 64),
        }
        for name, change in variants.items():
            with self.subTest(name):
                before = snapshot(self.studio.artifacts.root)
                with mock.patch.object(pp, 'assemble_packet', side_effect=lambda s, sel: change(real(s, sel))):
                    res = persist_packet(self.studio, self.run, 'case-a')
                self.assertEqual(res.status, NOT_RECORDED)
                self.assertIn('PRECHECK_MISMATCH', self.codes(res))
                self.assertEqual(snapshot(self.studio.artifacts.root), before)

    def test_t4_selection_accepts_other_ids_than_the_packet(self):
        with mock.patch.object(pp, 'assemble_packet',
                               side_effect=lambda s, sel: assemble_packet(s, sel._replace(accepted=(self.w.case_input,)))):
            res = persist_packet(self.studio, self.run, 'case-a')
        self.assertEqual(res.status, NOT_RECORDED)
        self.assertIn('PRECHECK_MISMATCH', self.codes(res))


class RepeatTests(PersistTestCase):
    def test_t5_t11_same_request_again_returns_the_existing_record(self):
        first = persist_packet(self.studio, self.run, 'case-a')
        before = snapshot(self.studio.artifacts.root)
        for _ in range(3):
            again = persist_packet(self.studio, self.run, 'case-a')
            self.assertEqual((again.status, again.wrote), (ALREADY_RECORDED, False))
            self.assertEqual((again.packet_artifact_id, again.transfer_id), (first.packet_artifact_id, first.transfer_id))
        self.assertEqual(snapshot(self.studio.artifacts.root), before)

    def test_t6_other_packet_for_an_edge_that_has_a_transfer(self):
        persist_packet(self.studio, self.run, 'case-a')
        before = snapshot(self.studio.artifacts.root)
        with mock.patch.object(pp, 'assemble_packet',
                               side_effect=lambda s, sel: assemble_packet(s, sel._replace(accepted=(self.w.case_input,)))):
            res = persist_packet(self.studio, self.run, 'case-a')
        self.assertEqual(res.status, NOT_RECORDED)
        self.assertEqual(self.codes(res)[0], 'EDGE_ALREADY_HAS_TRANSFER')
        self.assertTrue(res.needs_human)
        self.assertEqual(snapshot(self.studio.artifacts.root), before)

    def test_extra_packet_naming_a_recorded_transfer_is_not_already_recorded(self):
        done = persist_packet(self.studio, self.run, 'case-a')
        stored = self.studio.artifacts.read(done.packet_artifact_id)
        meta = stored.metadata()
        extra = self.studio.artifacts.create(content=stored.content, kind='PACKET', case_id=meta['provenance']['caseId'],
                                             source_kind='SYSTEM', received_via='SYSTEM_GENERATED', run_id=self.run,
                                             transfer_id=done.transfer_id, producer_node_id=meta['producerNodeId']).artifact_id
        res = persist_packet(self.studio, self.run, 'case-a')
        self.assertEqual(res.status, PARTIAL_STATE)
        self.assertIn('STRAY_PACKET', self.codes(res))
        self.assertIn(extra, res.reasons[0][1])

    def test_two_transfers_on_one_edge_are_a_partial_state(self):
        self.assertEqual(persist_packet(self.studio, self.run, 'case-a').status, RECORDED)
        self.w.send(self.run, 'case-a', [self.w.case_input, self.w.source_artifact])  # a second, hand-made transfer
        res = persist_packet(self.studio, self.run, 'case-a')
        self.assertEqual(res.status, PARTIAL_STATE)
        self.assertIn('MULTIPLE_TRANSFERS', self.codes(res))

    def test_t12_records_that_do_not_correspond_are_not_already_recorded(self):
        def edit_transfer(t):
            path = self.studio.records.dir / 'transfer' / f'{t["transferId"]}.json'
            rec = json.loads(path.read_bytes())
            rec['artifactIds'] = list(reversed(rec['artifactIds']))
            tamper(path, json.dumps(rec).encode())

        def edit_event(t):
            for p in self.studio.events.dir.iterdir():
                ev = json.loads(p.read_bytes())
                if ev['eventType'] == 'TRANSFER_CREATED' and ev['targetId'] == t['transferId']:
                    ev['payload']['packetHash'] = '0' * 64
                    tamper(p, json.dumps(ev).encode())

        def edit_packet_link(t):
            path = self.studio.records.dir / 'transfer' / f'{t["transferId"]}.json'
            rec = json.loads(path.read_bytes())
            rec['packetArtifactId'] = self.w.case_input
            tamper(path, json.dumps(rec).encode())

        for name, edit in {'transfer artifactIds': edit_transfer, 'TRANSFER_CREATED packetHash': edit_event,
                           'transfer names another artifact': edit_packet_link}.items():
            with self.subTest(name):
                run = self.w.start(self.w.trial('P001'))  # a fresh run for each edit
                self.assertEqual(persist_packet(self.studio, run, 'case-a').status, RECORDED)
                [t] = self.transfers('case-a', run)
                edit(t)
                res = persist_packet(self.studio, run, 'case-a')
                self.assertNotEqual(res.status, ALREADY_RECORDED)
                self.assertEqual(res.status, PARTIAL_STATE)


class PartialFailureTests(PersistTestCase):
    def test_t7_failure_before_the_artifact_commit_point(self):
        real = artifact_store.create_exclusive

        def fail_on_metadata(path, data, read_only=True):
            if path.name == 'artifact.json':
                raise OSError('simulated crash before commit')
            return real(path, data, read_only)
        with mock.patch.object(artifact_store, 'create_exclusive', fail_on_metadata):
            res = persist_packet(self.studio, self.run, 'case-a')
        self.assertEqual((res.status, self.codes(res), res.wrote), (PARTIAL_STATE, ['PACKET_INCOMPLETE'], True))
        self.assertEqual(self.studio.artifacts.inspect(res.packet_artifact_id).status, 'INCOMPLETE')
        before = snapshot(self.studio.artifacts.root)
        again = persist_packet(self.studio, self.run, 'case-a')
        self.assertEqual((again.status, again.needs_human), (PARTIAL_STATE, True))
        self.assertIn('PACKET_INCOMPLETE', self.codes(again))
        self.assertEqual(snapshot(self.studio.artifacts.root), before)  # nothing deleted, nothing new

    def test_t8_artifact_received_not_recorded(self):
        real = self.studio.events.append

        def fail_artifact_event(**kw):
            if kw['event_type'] == 'ARTIFACT_RECEIVED':
                raise OSError('simulated crash before event')
            return real(**kw)
        with mock.patch.object(self.studio.events, 'append', side_effect=fail_artifact_event):
            res = persist_packet(self.studio, self.run, 'case-a')
        self.assertEqual((res.status, self.codes(res)), (PARTIAL_STATE, ['PACKET_ORPHAN']))
        self.assertFalse(self.studio.artifacts.read(res.packet_artifact_id).event_linked)
        again = persist_packet(self.studio, self.run, 'case-a')
        self.assertEqual(again.status, PARTIAL_STATE)
        self.assertIn('PACKET_ORPHAN', self.codes(again))
        self.assertEqual(self.packets(), [res.packet_artifact_id])  # no second packet
        self.assertEqual(self.transfers('case-a'), [])

    def test_t9_failure_before_the_transfer_and_explicit_resume(self):
        with mock.patch.object(self.studio, 'record_transfer', side_effect=OSError('simulated crash before transfer')):
            res = persist_packet(self.studio, self.run, 'case-a')
        self.assertEqual((res.status, self.codes(res), res.resumable), (PARTIAL_STATE, ['TRANSFER_NOT_CREATED'], True))
        diag = persist_packet(self.studio, self.run, 'case-a')
        self.assertEqual((diag.status, self.codes(diag), diag.resumable), (PARTIAL_STATE, ['PACKET_WITHOUT_TRANSFER'], True))
        self.assertEqual((diag.packet_artifact_id, diag.transfer_id), (res.packet_artifact_id, res.transfer_id))
        self.assertEqual(self.transfers('case-a'), [])
        done = persist_packet(self.studio, self.run, 'case-a', transfer_id=res.transfer_id, packet_artifact_id=res.packet_artifact_id)
        self.assertEqual(done.status, RECORDED)
        self.assertEqual(self.packets(), [res.packet_artifact_id])
        self.assertEqual([t['transferId'] for t in self.transfers('case-a')], [res.transfer_id])
        self.assertEqual(persist_packet(self.studio, self.run, 'case-a').status, ALREADY_RECORDED)

    def test_resume_with_other_ids_is_refused(self):
        with mock.patch.object(self.studio, 'record_transfer', side_effect=OSError('simulated crash')):
            res = persist_packet(self.studio, self.run, 'case-a')
        bad = persist_packet(self.studio, self.run, 'case-a', transfer_id='XFER-other-001', packet_artifact_id=res.packet_artifact_id)
        self.assertEqual(bad.status, PARTIAL_STATE)
        self.assertEqual(self.transfers('case-a'), [])

    def test_t10_transfer_created_not_recorded(self):
        real = self.studio.events.append

        def fail_transfer_event(**kw):
            if kw['event_type'] == 'TRANSFER_CREATED':
                raise OSError('simulated crash before event')
            return real(**kw)
        with mock.patch.object(self.studio.events, 'append', side_effect=fail_transfer_event):
            res = persist_packet(self.studio, self.run, 'case-a')
        self.assertEqual((res.status, self.codes(res), res.resumable), (PARTIAL_STATE, ['TRANSFER_UNANCHORED'], False))
        before = snapshot(self.studio.artifacts.root)
        again = persist_packet(self.studio, self.run, 'case-a')
        self.assertEqual((again.status, again.needs_human), (PARTIAL_STATE, True))
        self.assertIn('TRANSFER_UNANCHORED', self.codes(again))
        self.assertEqual(snapshot(self.studio.artifacts.root), before)

    def test_t14_transfer_refused_by_the_existing_checks(self):
        self.studio.complete_run(self.run)
        res = persist_packet(self.studio, self.run, 'case-a')
        self.assertEqual((res.status, self.codes(res)), (PARTIAL_STATE, ['TRANSFER_REJECTED']))
        self.assertTrue(res.needs_human)
        self.assertEqual(self.packets(), [res.packet_artifact_id])  # the packet stays, it is not removed
        self.assertEqual(self.transfers('case-a'), [])

    def test_unexpected_error_propagates(self):
        with mock.patch.object(pp, 'assemble_packet', side_effect=KeyError('bug')):
            with self.assertRaises(KeyError):
                persist_packet(self.studio, self.run, 'case-a')


class StoreCheckTests(PersistTestCase):
    def test_t13_unrelated_store_problem_is_reported_not_attributed(self):
        other = self.studio.create_case('another question')
        note = self.studio.artifacts.create(content=b'note', kind='HUMAN_NOTE', case_id=other, source_kind='USER',
                                            received_via='MANUAL_PASTE').artifact_id
        tamper(self.studio.artifacts.artifacts_dir / note / 'payload.bin', b'changed')
        res = persist_packet(self.studio, self.run, 'case-a')
        self.assertEqual(res.status, RECORDED)
        self.assertFalse(res.store_check_complete)
        self.assertTrue(any(note in subject for _, subject, _ in res.store_problems))

    def test_t13_related_store_problem_fails_the_postcheck(self):
        real = pp.verify_store

        def with_related_problem(studio):
            from infrastructure.storage.event_log import Problem
            return list(real(studio)) + [Problem('BUNDLE_SR-17', f'run {self.run}', 'injected')]
        with mock.patch.object(pp, 'verify_store', side_effect=with_related_problem):
            res = persist_packet(self.studio, self.run, 'case-a')
        self.assertEqual((res.status, res.needs_human), (PARTIAL_STATE, True))
        self.assertEqual(self.codes(res), ['POSTCHECK_FAILED'])
