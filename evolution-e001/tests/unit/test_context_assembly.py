"""E001-F1/U3-1: run context and artifact candidate inventory (application/context_assembly.py).

Uses the E001-D scenario (tests/integration/support.py) on a temporary store. Nothing here selects
artifacts for a packet; the tests check classification, order, provenance flags and that the
inventory reads only.
"""
import copy
import json
from unittest import mock

from application.context_assembly import (INITIAL, OUT_OF_SCOPE, PACKET, RESPONSE, ContextAssemblyError, RunInventory,
                                          build_inventory, build_run_context)
from application.execution_context import new_id
from application.provenance import reconstruct_run
from domain.protocol.evaluator import ArtifactRef, evaluate_receive
from infrastructure.storage.event_log import SYSTEM_ACTOR_ID
from tests.integration.support import StudioTestCase
from tests.persistence.support import snapshot, tamper


class RunContextTests(StudioTestCase):
    def test_context_from_frozen_records(self):
        p1 = self.w.run_p001()
        inv = build_inventory(self.studio, p1['run'])
        ctx = inv.context
        self.assertEqual(dict(ctx.participants), {**{n: frozenset({'OBSERVER'}) for n in self.w.obs.values()},
                                                  self.w.human: frozenset({'SYNTHESIZER'})})
        self.assertEqual(ctx.system_node_ids, frozenset({SYSTEM_ACTOR_ID}))
        self.assertIn((SYSTEM_ACTOR_ID, self.w.obs['a']), ctx.route_edges)
        self.assertEqual(len(inv.edges), 6)

    def test_participants_is_read_only(self):
        ctx = build_inventory(self.studio, self.w.run_p001()['run']).context
        with self.assertRaises(TypeError):
            ctx.participants['NODE-intruder-1'] = frozenset({'OBSERVER'})

    def test_context_is_accepted_by_u1(self):
        p1 = self.w.run_p001()
        inv = build_inventory(self.studio, p1['run'])
        protocol = self.studio.records.get('protocol', inv.protocol_ref['id'], inv.protocol_ref['version']).record()
        a, b = self.w.obs['a'], self.w.obs['b']
        meta = self.studio.artifacts.read(p1['raw']['b']).metadata()
        d = evaluate_receive(protocol, inv.context, a, (SYSTEM_ACTOR_ID, a), ArtifactRef(meta['artifactId'], meta['kind'], meta['producerNodeId']))
        self.assertEqual((d.effect, d.deny_rule_ids), ('DENY', ('no-peer',)))

    def test_roles_are_a_union_over_slots(self):
        manifest = {'roleAssignments': [{'nodeId': 'NODE-x-001', 'role': 'OBSERVER'}, {'nodeId': 'NODE-x-001', 'role': 'SYNTHESIZER'}],
                    'nodes': [{'nodeId': SYSTEM_ACTOR_ID, 'kind': 'SYSTEM'}, {'nodeId': 'NODE-x-001', 'kind': 'MODEL'}]}
        route = {'edges': [{'fromNodeId': SYSTEM_ACTOR_ID, 'toNodeId': 'NODE-x-001'}]}
        before = copy.deepcopy((manifest, route))
        ctx = build_run_context(manifest, route)
        self.assertEqual(ctx.participants['NODE-x-001'], frozenset({'OBSERVER', 'SYNTHESIZER'}))
        self.assertEqual((manifest, route), before)  # inputs are not modified


class ClassificationTests(StudioTestCase):
    def setUp(self):
        super().setUp()
        self.p1 = self.w.run_p001()

    def by_id(self, inv):
        return {c.artifact_id: c for c in inv.candidates}

    def test_groups_after_a_full_p001_run(self):
        inv = build_inventory(self.studio, self.p1['run'])
        groups = [c.group for c in inv.candidates]
        self.assertEqual(groups.count(INITIAL), 2)
        self.assertEqual(groups.count(RESPONSE), 3)
        self.assertEqual(groups.count(PACKET), 6)
        self.assertTrue(all(c.provenance_ok for c in inv.candidates))
        cand = self.by_id(inv)
        for x in 'abc':
            self.assertEqual((cand[self.p1['raw'][x]].group, cand[self.p1['raw'][x]].producer_node_id), (RESPONSE, self.w.obs[x]))
        self.assertEqual(inv.unreadable, ())
        self.assertIsInstance(inv, RunInventory)

    def test_initial_order_follows_the_manifest(self):
        trial = self.w.trial('P002')
        run = self.studio.start_run(trial, initial_artifact_ids=[self.w.source_artifact, self.w.case_input])
        inv = build_inventory(self.studio, run)
        self.assertEqual([c.artifact_id for c in inv.candidates[:2]], [self.w.source_artifact, self.w.case_input])
        self.assertEqual([c.group for c in inv.candidates[:2]], [INITIAL, INITIAL])

    def test_rest_is_in_ascii_order(self):
        rest = [c.artifact_id for c in build_inventory(self.studio, self.p1['run']).candidates[2:]]
        self.assertEqual(rest, sorted(rest, key=lambda a: a.encode('ascii')))

    def test_out_of_scope_artifacts_are_listed_with_a_reason(self):
        note = self.studio.artifacts.create(content=b'case note', kind='HUMAN_NOTE', case_id=self.w.case, source_kind='USER',
                                            received_via='MANUAL_PASTE').artifact_id
        run_note = self.studio.artifacts.create(content=b'run note', kind='HUMAN_NOTE', case_id=self.w.case, source_kind='USER',
                                                received_via='MANUAL_PASTE', run_id=self.p1['run']).artifact_id
        p2 = self.w.run_p002()
        cand = self.by_id(build_inventory(self.studio, self.p1['run']))
        self.assertEqual((cand[note].group, cand[note].problems[0][0]), (OUT_OF_SCOPE, 'NOT_IN_RUN'))
        self.assertEqual((cand[run_note].group, cand[run_note].problems[0][0]), (OUT_OF_SCOPE, 'KIND_NOT_COLLECTED'))
        self.assertTrue(cand[run_note].provenance_ok)
        self.assertEqual(cand[p2['raw']['a']].group, OUT_OF_SCOPE)  # another run of the same case

    def test_other_cases_are_not_listed(self):
        other = self.studio.create_case('another question')
        foreign = self.studio.artifacts.create(content=b'x', kind='CASE_INPUT', case_id=other, source_kind='USER',
                                               received_via='MANUAL_PASTE').artifact_id
        self.assertNotIn(foreign, self.by_id(build_inventory(self.studio, self.p1['run'])))


class ProvenanceFlagTests(StudioTestCase):
    def setUp(self):
        super().setUp()
        self.p1 = self.w.run_p001()

    def candidate(self, aid):
        return next(c for c in build_inventory(self.studio, self.p1['run']).candidates if c.artifact_id == aid)

    def test_response_from_a_node_that_was_not_sent_the_packet(self):
        bad = self.w.respond(self.p1['run'], self.p1['transfers']['a'], self.w.obs['c'], 'C answering A')
        c = self.candidate(bad)
        self.assertEqual(c.group, RESPONSE)
        self.assertFalse(c.provenance_ok)
        self.assertIn('PROVENANCE_MISMATCH', {code for code, _ in c.problems})

    def test_response_naming_another_run_but_this_runs_transfer(self):
        other_run = self.w.start(self.w.trial('P002'))
        bad = self.studio.artifacts.create(content=b'misfiled', kind='RAW_RESPONSE', case_id=self.w.case, source_kind='AI',
                                           received_via='MANUAL_PASTE', run_id=other_run, transfer_id=self.p1['transfers']['a'],
                                           producer_node_id=self.w.obs['a']).artifact_id
        c = self.candidate(bad)  # claims this run through its transfer, so it is listed, not skipped
        self.assertEqual((c.group, c.provenance_ok), (RESPONSE, False))

    def test_orphan_response_is_flagged(self):
        with mock.patch.object(self.studio.events, 'append', side_effect=OSError('crash')):
            with self.assertRaises(OSError):
                self.w.respond(self.p1['run'], self.p1['transfers']['b'], self.w.obs['b'], 'second answer')
        orphan = next(a for a in self.studio.artifacts.list_ids() if not self.studio.artifacts.read(a).event_linked)
        c = self.candidate(orphan)
        self.assertFalse(c.provenance_ok)
        self.assertIn('ORPHAN', {code for code, _ in c.problems})


class BrokenDataTests(StudioTestCase):
    def setUp(self):
        super().setUp()
        self.p1 = self.w.run_p001()

    def test_unknown_run(self):
        with self.assertRaises(ContextAssemblyError):
            build_inventory(self.studio, 'RUN-does-not-exist')

    def test_tampered_initial_artifact_stops_the_inventory(self):
        tamper(self.studio.artifacts.artifacts_dir / self.w.case_input / 'payload.bin', b'changed question')
        with self.assertRaises(ContextAssemblyError):
            build_inventory(self.studio, self.p1['run'])

    def test_tampered_manifest_stops_the_inventory(self):
        mid = self.studio.manifest_of(self.p1['run'])['runManifestId']
        path = self.studio.records.dir / 'runManifest' / f'{mid}.json'
        rec = json.loads(path.read_bytes())
        rec['configuration'] = {'changed': True}
        tamper(path, json.dumps(rec).encode())
        with self.assertRaises(ContextAssemblyError):
            build_inventory(self.studio, self.p1['run'])

    def _run_started(self, run_id):
        return next(e for e in self.studio.events.read_all()[0] if e['eventType'] == 'RUN_STARTED' and e['targetId'] == run_id)

    def test_manifest_naming_another_run_stops_the_inventory(self):
        # written past start_run: R2's manifest names R1, and R2's RUN_STARTED holds that manifest's real hash
        m1 = self.studio.manifest_of(self.p1['run'])
        r2, m2 = new_id('RUN'), new_id('MAN')
        stored = self.studio.records.put('runManifest', dict(m1, runManifestId=m2))
        self.studio.records.put('run', dict(self.studio.records.get('run', self.p1['run']).record(), runId=r2, runManifestId=m2))
        self.studio.events.append(event_type='RUN_STARTED', target_type='run', target_id=r2,
                                  payload=dict(self._run_started(self.p1['run'])['payload'], manifestHash=stored.record_hash))
        self.assertEqual(reconstruct_run(self.studio, r2)[1], [])  # the frozen hashes agree
        with self.assertRaises(ContextAssemblyError) as cm:
            build_inventory(self.studio, r2)
        self.assertTrue(any('different run' in p for p in cm.exception.problems))

    def _point_second_run_at_first_manifest(self):
        r2 = self.w.start(self.p1['trial'])  # same trial, so the trial check cannot be what stops it
        m1 = self.studio.manifest_of(self.p1['run'])['runManifestId']
        path = self.studio.records.dir / 'run' / f'{r2}.json'
        rec = json.loads(path.read_bytes())
        rec['runManifestId'] = m1
        tamper(path, json.dumps(rec).encode())
        return r2, m1

    def test_run_record_pointing_at_another_manifest_stops_the_inventory(self):
        r2, _ = self._point_second_run_at_first_manifest()  # RUN_STARTED still holds R2's own manifest hash
        with self.assertRaises(ContextAssemblyError) as cm:
            build_inventory(self.studio, r2)
        self.assertTrue(any('MANIFEST_MISMATCH' in p for p in cm.exception.problems))

    def test_run_record_and_event_rewritten_to_another_manifest_stops_the_inventory(self):
        r2, m1 = self._point_second_run_at_first_manifest()
        h1 = self.studio.records.get('runManifest', m1).record_hash
        event = self._run_started(r2)
        path = next(p for p in self.studio.events.dir.iterdir() if json.loads(p.read_bytes())['eventId'] == event['eventId'])
        event['payload']['manifestHash'] = h1
        tamper(path, json.dumps(event).encode())
        self.assertEqual(reconstruct_run(self.studio, r2)[1], [])  # the frozen hashes agree again
        with self.assertRaises(ContextAssemblyError) as cm:
            build_inventory(self.studio, r2)
        self.assertTrue(any('different run' in p for p in cm.exception.problems))

    def test_corrupt_response_not_carried_anywhere_is_listed_as_unreadable(self):
        extra = self.w.respond(self.p1['run'], self.p1['transfers']['b'], self.w.obs['b'], 'second answer from B')
        tamper(self.studio.artifacts.artifacts_dir / extra / 'payload.bin', b'changed answer')
        inv = build_inventory(self.studio, self.p1['run'])
        self.assertEqual([u.artifact_id for u in inv.unreadable], [extra])
        self.assertIn('HASH_MISMATCH', {code for code, _ in inv.unreadable[0].problems})
        self.assertNotIn(extra, [c.artifact_id for c in inv.candidates])

    def test_corrupt_artifact_carried_by_a_transfer_stops_the_inventory(self):
        # raw C is carried on syn-c, so the run itself is no longer reconstructable
        tamper(self.studio.artifacts.artifacts_dir / self.p1['raw']['c'] / 'payload.bin', b'changed answer')
        with self.assertRaises(ContextAssemblyError) as cm:
            build_inventory(self.studio, self.p1['run'])
        self.assertTrue(any('ARTIFACT_UNVERIFIED' in p for p in cm.exception.problems))


class ReadOnlyAndDeterminismTests(StudioTestCase):
    def test_same_store_same_inventory(self):
        p1 = self.w.run_p001()
        self.assertEqual(build_inventory(self.studio, p1['run']), build_inventory(self.studio, p1['run']))

    def test_inventory_writes_nothing(self):
        p1 = self.w.run_p001()
        root = self.studio.artifacts.root
        before, events = snapshot(root), len(self.studio.events.read_all()[0])
        transfers = self.studio.records.list('transfer')
        build_inventory(self.studio, p1['run'])
        self.assertEqual(snapshot(root), before)
        self.assertEqual(len(self.studio.events.read_all()[0]), events)
        self.assertEqual(self.studio.records.list('transfer'), transfers)
