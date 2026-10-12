"""E001-F1/U3-2: candidate selection (application/candidate_selection.py).

Acceptance conditions A1-A13 of reports/U3-2_MINIMAL_IMPLEMENTATION_SPEC_002.md (review copy). Every
test runs on the E001-D scenario in a temporary store; U1 calls are recorded with a pass-through spy.
"""
import json
from unittest import mock

from application import candidate_selection as cs
from application.candidate_selection import EVALUATED, INCOMPLETE, select_candidates
from application.context_assembly import build_inventory
from domain.protocol import evaluator
from domain.protocol.evaluator import ArtifactRef, EvaluationInputInvalid
from infrastructure.storage.event_log import SYSTEM_ACTOR_ID
from tests.integration.support import SECRETS, V, StudioTestCase, rule
from tests.persistence.support import snapshot, tamper


class SelectionTestCase(StudioTestCase):
    def setUp(self):
        super().setUp()
        self.p1 = self.w.run_p001()

    def select(self, edge_id, run_id=None):
        """Run select_candidates and return (selection, ids passed to U1)."""
        seen = []
        real = evaluator.evaluate_receive

        def spy(protocol, context, subject, edge, artifact):
            seen.append(artifact.artifact_id)
            return real(protocol, context, subject, edge, artifact)
        with mock.patch.object(cs.evaluator, 'evaluate_receive', side_effect=spy):
            sel = select_candidates(self.studio, run_id or self.p1['run'], edge_id)
        return sel, seen

    def rejected(self, sel):
        return {r.artifact_id: (r.stage, r.code) for r in sel.rejected}

    def assert_partition(self, sel, run_id=None):
        """Every inventory candidate is decided exactly once."""
        inv = build_inventory(self.studio, run_id or self.p1['run'])
        decided = list(sel.accepted) + [r.artifact_id for r in sel.rejected] + [u.artifact_id for u in sel.unresolved]
        self.assertCountEqual(decided, [c.artifact_id for c in inv.candidates])


class AcceptanceTests(SelectionTestCase):
    def test_a1_case_edge_accepts_the_initial_inputs_in_manifest_order(self):
        sel, seen = self.select('case-a')
        self.assertEqual(sel.status, EVALUATED)
        self.assertEqual(sel.accepted, (self.w.case_input, self.w.source_artifact))
        self.assertEqual((sel.from_node_id, sel.to_node_id), (SYSTEM_ACTOR_ID, self.w.obs['a']))
        self.assertEqual(seen, [self.w.case_input, self.w.source_artifact])
        self.assert_partition(sel)

    def test_a2_peer_answers_never_reach_u1_on_a_case_edge(self):
        sel, seen = self.select('case-a')
        rej = self.rejected(sel)
        for x in 'bc':
            self.assertEqual(rej[self.p1['raw'][x]], ('S3', 'KIND_NOT_ALLOWED_ON_EDGE'))
            self.assertNotIn(self.p1['raw'][x], seen)
        texts = [self.studio.artifacts.read(a).text() for a in sel.accepted]
        self.assertFalse(any(SECRETS[x] in t for t in texts for x in 'bc'))

    def test_a2b_p002_answer_two_steps_back_is_a_protocol_deny(self):
        p2 = self.w.run_p002()
        sel, seen = self.select('b-to-c', p2['run'])
        rej = self.rejected(sel)
        self.assertEqual(rej[p2['raw']['a']], ('S4', 'PROTOCOL_DENY'))
        self.assertIn(p2['raw']['a'], seen)
        detail = next(r.detail for r in sel.rejected if r.artifact_id == p2['raw']['a'])
        self.assertIn(('no-peer', ''), detail)
        self.assertEqual(sel.accepted, (p2['raw']['b'],))
        self.assert_partition(sel, p2['run'])

    def test_a3_case_input_added_after_run_start_is_not_selectable(self):
        late = self.studio.artifacts.create(content=b'late question', kind='CASE_INPUT', case_id=self.w.case, source_kind='USER',
                                            received_via='MANUAL_PASTE').artifact_id
        sel, seen = self.select('case-a')
        self.assertEqual(self.rejected(sel)[late], ('S1', 'GROUP_NOT_SELECTABLE'))
        self.assertNotIn(late, seen)
        self.assertNotIn(late, sel.accepted)
        # U1 alone would allow it: the selection, not U1, keeps it out
        protocol = self.studio.records.get('protocol', 'PROTO-P001', '1.0').record()
        inv = build_inventory(self.studio, self.p1['run'])
        d = evaluator.evaluate_receive(protocol, inv.context, self.w.obs['a'], (SYSTEM_ACTOR_ID, self.w.obs['a']),
                                       ArtifactRef(late, 'CASE_INPUT', None))
        self.assertEqual(d.effect, 'ALLOW')

    def test_a4_response_without_transfer_is_rejected_before_u1(self):
        loose = self.studio.artifacts.create(content=b'answer with no transfer', kind='RAW_RESPONSE', case_id=self.w.case,
                                             source_kind='AI', received_via='MANUAL_PASTE', run_id=self.p1['run'],
                                             producer_node_id=self.w.obs['b']).artifact_id
        inv_cand = next(c for c in build_inventory(self.studio, self.p1['run']).candidates if c.artifact_id == loose)
        # DR section 3 inference, now executed: U3-1 lists it as a RESPONSE with provenance_ok=True
        self.assertEqual((inv_cand.group, inv_cand.provenance_ok), ('RESPONSE', True))
        sel, seen = self.select('syn-b')
        self.assertEqual(self.rejected(sel)[loose], ('S2', 'RESPONSE_WITHOUT_TRANSFER'))
        self.assertNotIn(loose, seen)
        self.assertEqual(sel.accepted, (self.p1['raw']['b'],))  # and it caused no ambiguity for B

    def test_a5_response_naming_another_run(self):
        other = self.w.start(self.w.trial('P002'))
        bad = self.studio.artifacts.create(content=b'misfiled', kind='RAW_RESPONSE', case_id=self.w.case, source_kind='AI',
                                           received_via='MANUAL_PASTE', run_id=other, transfer_id=self.p1['transfers']['a'],
                                           producer_node_id=self.w.obs['a']).artifact_id
        sel, seen = self.select('syn-a')
        self.assertEqual(self.rejected(sel)[bad], ('S2', 'PROVENANCE_MISMATCH'))
        self.assertNotIn(bad, seen)

    def test_a5b_response_naming_this_run_but_another_runs_transfer(self):
        p2 = self.w.run_p002()
        bad = self.studio.artifacts.create(content=b'wrong transfer', kind='RAW_RESPONSE', case_id=self.w.case, source_kind='AI',
                                           received_via='MANUAL_PASTE', run_id=self.p1['run'], transfer_id=p2['transfers']['case-a'],
                                           producer_node_id=self.w.obs['a']).artifact_id
        sel, seen = self.select('syn-a')
        self.assertEqual(self.rejected(sel)[bad], ('S2', 'TRANSFER_NOT_IN_RUN'))
        self.assertNotIn(bad, seen)

    def test_a6_orphan_response(self):
        with mock.patch.object(self.studio.events, 'append', side_effect=OSError('crash')):
            with self.assertRaises(OSError):
                self.w.respond(self.p1['run'], self.p1['transfers']['b'], self.w.obs['b'], 'second answer')
        orphan = next(a for a in self.studio.artifacts.list_ids() if not self.studio.artifacts.read(a).event_linked)
        sel, seen = self.select('syn-b')
        self.assertEqual(self.rejected(sel)[orphan], ('S2', 'PROVENANCE_NOT_OK'))
        self.assertIn(('ORPHAN', 'no ARTIFACT_RECEIVED event records this artifact'),
                      next(r.detail for r in sel.rejected if r.artifact_id == orphan))
        self.assertEqual(sel.accepted, (self.p1['raw']['b'],))

    def test_a7_two_answers_from_one_producer_are_unresolved_others_still_decided(self):
        second = self.w.respond(self.p1['run'], self.p1['transfers']['b'], self.w.obs['b'], 'B again')
        sel, seen = self.select('syn-b')
        self.assertEqual(sel.status, EVALUATED)
        self.assertCountEqual([u.artifact_id for u in sel.unresolved], [self.p1['raw']['b'], second])
        self.assertTrue(all(u.code == 'MULTIPLE_RESPONSES_SAME_PRODUCER' for u in sel.unresolved))
        rej = self.rejected(sel)
        for x in 'ac':  # unrelated candidates keep their own decision
            self.assertEqual(rej[self.p1['raw'][x]], ('S4', 'PROTOCOL_DENY'))
        self.assertEqual(rej[self.w.case_input], ('S3', 'KIND_NOT_ALLOWED_ON_EDGE'))
        self.assertEqual(sel.accepted, ())
        self.assert_partition(sel)

    def test_a7b_a_response_failing_s2_does_not_make_the_producer_ambiguous(self):
        stray = self.studio.artifacts.create(content=b'B, no transfer', kind='RAW_RESPONSE', case_id=self.w.case, source_kind='AI',
                                             received_via='MANUAL_PASTE', run_id=self.p1['run'], producer_node_id=self.w.obs['b']).artifact_id
        sel, _ = self.select('syn-b')
        self.assertEqual(sel.unresolved, ())
        self.assertEqual(sel.accepted, (self.p1['raw']['b'],))
        self.assertEqual(self.rejected(sel)[stray], ('S2', 'RESPONSE_WITHOUT_TRANSFER'))

    def test_a8_unreadable_initial_input_makes_the_call_incomplete(self):
        tamper(self.studio.artifacts.artifacts_dir / self.w.case_input / 'payload.bin', b'changed question')
        sel, seen = self.select('case-a')
        self.assertEqual(sel.status, INCOMPLETE)
        self.assertEqual((sel.accepted, sel.rejected, sel.unresolved), ((), (), ()))
        self.assertTrue(sel.problems and all(code == 'INVENTORY_FAILED' for code, _ in sel.problems))
        self.assertEqual(seen, [])

    def test_a9_corrupt_response_carried_by_no_transfer_is_a_diagnostic_not_a_stop(self):
        before, _ = self.select('syn-b')
        # an answer to B's case packet (provenance.transferId is set) that no transfer carries onward
        extra = self.w.respond(self.p1['run'], self.p1['transfers']['b'], self.w.obs['b'], 'second answer from B')
        carried = {a for t in self.studio.records.records('transfer') for a in [t['packetArtifactId'], *t['artifactIds']]}
        self.assertNotIn(extra, carried)
        payload = self.studio.artifacts.artifacts_dir / extra / 'payload.bin'
        tamper(payload, b'changed answer')
        self.assertNotEqual(self.studio.artifacts.inspect(extra).status, 'OK')  # the corruption is real
        sel, _ = self.select('syn-b')
        self.assertEqual(sel.status, EVALUATED)
        self.assertEqual([d[1] for d in sel.diagnostics], [extra])
        self.assertNotIn(extra, [r.artifact_id for r in sel.rejected] + [u.artifact_id for u in sel.unresolved] + list(sel.accepted))
        # every other candidate is decided exactly as before the corrupt answer existed
        self.assertEqual((sel.accepted, sel.rejected, sel.unresolved), (before.accepted, before.rejected, before.unresolved))
        self.assertEqual(sel.accepted, (self.p1['raw']['b'],))

    def test_a10_edge_not_in_route(self):
        sel, seen = self.select('a-to-b')  # a P002 edge, not in the P001 route
        self.assertEqual((sel.status, sel.problems[0][0]), (INCOMPLETE, 'EDGE_NOT_IN_ROUTE'))
        self.assertEqual(seen, [])

    def test_a10_invalid_protocol(self):
        s, w = self.studio, self.w
        case_in = rule('case-input', {'type': 'ANY'}, 'RECEIVE', ['CASE_INPUT', 'SOURCE_CONTENT'], 'NONE', 'ALLOW')
        bad = s.register_protocol({'schemaVersion': V, 'protocolId': 'PROTO-DUP', 'version': '1.0', 'defaultEffect': 'DENY',
                                   'conflictResolution': 'DENY_OVERRIDES', 'violationAction': 'BLOCK', 'rules': [case_in, case_in]})
        route = s.records.get('route', 'ROUTE-P001', '1.0').record()
        dup_route = s.register_route(dict(route, routeId='ROUTE-DUP', protocolRef=bad))
        trial = s.create_trial(w.case, w.ds, 'duplicate rule ids', w.p001, dup_route, bad)
        run = w.start(trial)
        sel, _ = self.select('case-a', run)
        self.assertEqual((sel.status, sel.problems[0][0]), (INCOMPLETE, 'PROTOCOL_INVALID'))

    def test_a10_evaluation_input_invalid_is_not_a_deny(self):
        with mock.patch.object(cs.evaluator, 'evaluate_receive', side_effect=EvaluationInputInvalid(['forced'])):
            sel = select_candidates(self.studio, self.p1['run'], 'case-a')
        self.assertEqual((sel.status, sel.problems), (INCOMPLETE, (('EVALUATION_INPUT_INVALID', 'forced'),)))
        self.assertEqual(sel.rejected, ())

    def test_a11_initial_case_input_on_a_raw_response_edge(self):
        sel, seen = self.select('syn-a')
        self.assertEqual(self.rejected(sel)[self.w.case_input], ('S3', 'KIND_NOT_ALLOWED_ON_EDGE'))
        self.assertNotIn(self.w.case_input, seen)
        # U1 by itself would ALLOW it on this edge (case-input rule: ANY, NONE, ALWAYS)
        protocol = self.studio.records.get('protocol', 'PROTO-P001', '1.0').record()
        inv = build_inventory(self.studio, self.p1['run'])
        d = evaluator.evaluate_receive(protocol, inv.context, self.w.human, (self.w.obs['a'], self.w.human),
                                       ArtifactRef(self.w.case_input, 'CASE_INPUT', None))
        self.assertEqual(d.effect, 'ALLOW')
        self.assertEqual(sel.accepted, (self.p1['raw']['a'],))

    def test_a12_selection_writes_nothing(self):
        root = self.studio.artifacts.root
        before, events = snapshot(root), len(self.studio.events.read_all()[0])
        transfers = self.studio.records.list('transfer')
        for edge in ('case-a', 'syn-b'):
            select_candidates(self.studio, self.p1['run'], edge)
        self.assertEqual(snapshot(root), before)
        self.assertEqual(len(self.studio.events.read_all()[0]), events)
        self.assertEqual(self.studio.records.list('transfer'), transfers)

    def test_a13_same_store_same_selection(self):
        self.assertEqual(select_candidates(self.studio, self.p1['run'], 'syn-c'),
                         select_candidates(self.studio, self.p1['run'], 'syn-c'))


class ContractTests(SelectionTestCase):
    def test_packets_and_out_of_scope_are_rejected_at_s1(self):
        sel, _ = self.select('case-a')
        rej = self.rejected(sel)
        for x in 'abc':
            self.assertEqual(rej[self.p1['packets'][x]], ('S1', 'GROUP_NOT_SELECTABLE'))

    def test_submit_rules_are_listed_as_unevaluated(self):
        s, w = self.studio, self.w
        rules = [rule('case-input', {'type': 'ANY'}, 'RECEIVE', ['CASE_INPUT', 'SOURCE_CONTENT'], 'NONE', 'ALLOW'),
                 rule('obs-submit', {'type': 'ROLE', 'value': 'OBSERVER'}, 'SUBMIT', ['RAW_RESPONSE'], 'SELF', 'ALLOW')]
        proto = s.register_protocol({'schemaVersion': V, 'protocolId': 'PROTO-SUB', 'version': '1.0', 'defaultEffect': 'DENY',
                                     'conflictResolution': 'DENY_OVERRIDES', 'violationAction': 'BLOCK', 'rules': rules})
        route = s.records.get('route', 'ROUTE-P001', '1.0').record()
        r = s.register_route(dict(route, routeId='ROUTE-SUB', protocolRef=proto))
        run = w.start(s.create_trial(w.case, w.ds, 'submit listed', w.p001, r, proto))
        sel = select_candidates(s, run, 'syn-a')  # no candidate reaches U1 here
        self.assertEqual(sel.unevaluated_rule_ids, ('obs-submit',))

    def test_inventory_changed_under_the_selection_is_incomplete(self):
        real = build_inventory

        def stale(studio, run_id):
            inv = real(studio, run_id)
            cands = tuple(c._replace(content_hash='0' * 64) if c.group == 'RESPONSE' else c for c in inv.candidates)
            return inv._replace(candidates=cands)
        with mock.patch.object(cs, 'build_inventory', side_effect=stale):
            sel = select_candidates(self.studio, self.p1['run'], 'syn-a')
        self.assertEqual((sel.status, sel.problems[0][0]), (INCOMPLETE, 'INVENTORY_CHANGED'))

    def test_tampered_manifest_is_incomplete(self):
        mid = self.studio.manifest_of(self.p1['run'])['runManifestId']
        path = self.studio.records.dir / 'runManifest' / f'{mid}.json'
        rec = json.loads(path.read_bytes())
        rec['configuration'] = {'changed': True}
        tamper(path, json.dumps(rec).encode())
        sel, seen = self.select('case-a')
        self.assertEqual((sel.status, sel.problems[0][0]), (INCOMPLETE, 'INVENTORY_FAILED'))
        self.assertEqual(seen, [])
