"""E001-D: Case / Trial / Run records, the reserved system node, Node/Role/Provider separation,
run state derived from events, budget and retry."""
import json

from application.execution_context import (BudgetExceeded, ContextReferenceError, ExecutionContextError, RunNotRunning,
                                           Studio, SystemActorConflict)
from infrastructure.storage.event_log import SYSTEM_ACTOR_ID
from tests.integration.support import V, StudioTestCase
from tests.persistence.support import tamper


class SystemActorTests(StudioTestCase):
    def test_reserved_system_node_is_registered_as_system(self):
        node = self.studio.records.get('node', SYSTEM_ACTOR_ID).record()
        self.assertEqual(node['kind'], 'SYSTEM')

    def test_reserved_id_cannot_be_registered_again(self):
        with self.assertRaises(SystemActorConflict):
            self.studio.register_node('HUMAN', 'Impostor', node_id=SYSTEM_ACTOR_ID)

    def test_altered_system_node_is_refused_on_open(self):
        path = self.studio.records.dir / 'node' / f'{SYSTEM_ACTOR_ID}.json'
        rec = json.loads(path.read_bytes())
        rec['displayName'] = 'Someone else'
        tamper(path, json.dumps(rec).encode())
        with self.assertRaises(SystemActorConflict):
            Studio(self.studio.artifacts.root)

    def test_system_node_cannot_fill_a_role(self):
        w = self.w
        route = {'schemaVersion': V, 'routeId': 'ROUTE-bad', 'version': '1.0', 'patternRef': w.p001, 'protocolRef': w.proto1,
                 'slotBindings': [{'slotId': s, 'nodeId': n, 'providerId': w.provider}
                                  for s, n in zip(['obsA', 'obsB', 'obsC', 'synth'], [SYSTEM_ACTOR_ID, w.obs['b'], w.obs['c'], w.human])],
                 'edges': [{'edgeId': 'e', 'fromNodeId': SYSTEM_ACTOR_ID, 'toNodeId': w.obs['b'], 'allowedArtifactKinds': ['CASE_INPUT'], 'order': 1}]}
        with self.assertRaises(SystemActorConflict):
            self.studio.register_route(route)


class NodeRoleProviderTests(StudioTestCase):
    def test_node_holds_no_role_or_provider(self):
        node = self.studio.records.get('node', self.w.obs['a']).record()
        self.assertEqual(set(node), {'schemaVersion', 'nodeId', 'kind', 'displayName', 'createdAt'})

    def test_role_comes_from_pattern_and_provider_from_route_binding(self):
        out = self.w.run_p001()
        man = self.studio.manifest_of(out['run'])
        a = next(x for x in man['roleAssignments'] if x['nodeId'] == self.w.obs['a'])
        self.assertEqual((a['slotId'], a['role'], a['providerId']), ('obsA', 'OBSERVER', self.w.provider))
        h = next(x for x in man['roleAssignments'] if x['nodeId'] == self.w.human)
        self.assertEqual(h['role'], 'SYNTHESIZER')  # a human node with a manual provider is still not the provider

    def test_manual_provider_cannot_claim_enforced(self):
        with self.assertRaises(ExecutionContextError):
            self.studio.register_provider('MANUAL_FILE', 'ENFORCED')

    def test_route_must_cover_pattern_slots(self):
        w = self.w
        route = {'schemaVersion': V, 'routeId': 'ROUTE-short', 'version': '1.0', 'patternRef': w.p001, 'protocolRef': w.proto1,
                 'slotBindings': [{'slotId': 'obsA', 'nodeId': w.obs['a'], 'providerId': w.provider}],
                 'edges': [{'edgeId': 'e', 'fromNodeId': SYSTEM_ACTOR_ID, 'toNodeId': w.obs['a'], 'allowedArtifactKinds': ['CASE_INPUT'], 'order': 1}]}
        with self.assertRaises(ContextReferenceError):
            self.studio.register_route(route)


class CaseTrialTests(StudioTestCase):
    def creation_event(self, target_id):
        return next(e for e in self.studio.events.read_all()[0] if e['targetId'] == target_id)

    def test_creation_events_anchor_record_hashes(self):
        trial = self.w.trial()
        for entity, rid, etype in (('case', self.w.case, 'CASE_CREATED'), ('trial', trial, 'TRIAL_CREATED'),
                                   ('source', self.w.source, 'SOURCE_REGISTERED')):
            ev = self.creation_event(rid)
            self.assertEqual(ev['eventType'], etype)
            self.assertEqual(ev['payload']['recordHash'], self.studio.records.get(entity, rid).record_hash)

    def test_trial_must_agree_with_route_and_case(self):
        w = self.w
        with self.assertRaises(ContextReferenceError):
            self.studio.create_trial(w.case, w.ds, 'h', w.p001, w.route2, w.proto1)  # route built on P002
        other_case = self.studio.create_case('another question')
        other_ds = self.studio.create_design_space(other_case, 'other')
        with self.assertRaises(ContextReferenceError):
            self.studio.create_trial(w.case, other_ds, 'h', w.p001, w.route1, w.proto1)

    def test_source_status_is_derived_from_evidence(self):
        s, w = self.studio, self.w
        verified = s.artifacts.read(w.source_artifact).content_hash
        cases = {
            'VERIFIED': dict(content_artifact_id=w.source_artifact),
            'MISMATCH': dict(content_artifact_id=w.source_artifact, claimed_hash='0' * 64),
            'CLAIMED_ONLY': dict(claimed_hash=verified),
            'UNVERIFIED': dict(),
        }
        for expected, kw in cases.items():
            with self.subTest(expected):
                sid = s.register_source(w.case, 'TEXT', 'note', **kw)
                self.assertEqual(s.records.get('source', sid).record()['verificationStatus'], expected)

    def test_access_classification_is_stored_as_given(self):
        rec = self.studio.records.get('source', self.w.source).record()
        self.assertEqual(rec['accessClassification'], 'PUBLIC')  # D-07: provisional E001 interpretation, unchanged


class RunStateTests(StudioTestCase):
    def test_run_state_comes_from_events_and_the_record_is_never_rewritten(self):
        run = self.w.start(self.w.trial())
        record_before = (self.studio.records.dir / 'run' / f'{run}.json').read_bytes()
        self.assertEqual(self.studio.run_status(run), 'RUNNING')
        self.studio.complete_run(run)
        self.assertEqual(self.studio.run_status(run), 'COMPLETED')
        self.assertEqual((self.studio.records.dir / 'run' / f'{run}.json').read_bytes(), record_before)
        self.assertEqual(json.loads(record_before)['status'], 'RUNNING')  # creation-time value (D-20)

    def test_finished_run_cannot_change_or_take_transfers(self):
        run = self.w.start(self.w.trial())
        self.studio.fail_run(run, 'operator stopped')
        self.assertEqual(self.studio.run_status(run), 'FAILED')
        for action in (lambda: self.studio.complete_run(run), lambda: self.studio.fail_run(run, 'again'),
                       lambda: self.w.send(run, 'case-a', [self.w.case_input])):
            with self.assertRaises(RunNotRunning):
                action()

    def test_budget_limits_runs_per_trial(self):
        trial = self.w.trial(max_runs=2)
        self.w.start(trial)
        self.w.start(trial)
        with self.assertRaises(BudgetExceeded):
            self.w.start(trial)
        self.assertEqual(len(self.studio.runs_of_trial(trial)), 2)

    def test_retry_is_a_new_run_of_the_same_trial(self):
        trial = self.w.trial()
        first = self.w.start(trial)
        self.studio.fail_run(first, 'timeout')
        retry = self.w.start(trial, retry_of_run_id=first)
        self.assertNotEqual(retry, first)
        self.assertEqual(self.studio.records.get('run', retry).record()['retryOfRunId'], first)
        self.assertEqual(self.studio.run_status(first), 'FAILED')
        with self.assertRaises(ContextReferenceError):
            self.w.start(self.w.trial('P002'), retry_of_run_id=first)
