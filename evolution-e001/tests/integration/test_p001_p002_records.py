"""E001-D: P001 and P002 on the same core — records show who saw what, in which order.

Packet contents here are hand-made (assembly is E001-F). The SECRET markers prove that the
records, not the prompt wording, show which observer could have seen which artifact."""
from application import provenance as pv
from tests.integration.support import SECRETS, V, StudioTestCase
from infrastructure.storage.event_log import SYSTEM_ACTOR_ID


class SameCoreTests(StudioTestCase):
    def test_one_case_runs_as_p001_and_p002(self):
        p1, p2 = self.w.run_p001(), self.w.run_p002()
        tree = pv.trace_case(self.studio, self.w.case)
        self.assertEqual(len(tree['trials']), 2)
        patterns = {self.studio.manifest_of(p['run'])['patternRef']['id'] for p in (p1, p2)}
        self.assertEqual(patterns, {'PAT-P001', 'PAT-P002'})
        self.assertEqual(pv.verify_store(self.studio), [])


class P001IndependenceTests(StudioTestCase):
    def setUp(self):
        super().setUp()
        self.p1 = self.w.run_p001()

    def test_no_observer_packet_carries_a_peer_observation(self):
        entries, violations = pv.independence_report(self.studio, self.p1['run'])
        self.assertEqual(violations, [])
        self.assertEqual(len(entries), 3)
        for e in entries:
            self.assertEqual(e['peerArtifacts'], [])
            self.assertEqual(e['isolationAssurance'], 'PACKET_CONTROLLED')  # never ENFORCED for a manual provider

    def test_secret_markers_reach_only_their_own_observer(self):
        for x, secret in SECRETS.items():
            for y in 'abc':
                packet = self.studio.artifacts.read(self.p1['packets'][y]).text()
                self.assertNotIn(secret, packet, f'{secret} in packet for {y}')
            self.assertIn(secret, self.studio.artifacts.read(self.p1['raw'][x]).text())

    def test_route_refuses_a_peer_observation_on_an_observer_edge(self):
        from application.execution_context import ContextReferenceError
        with self.assertRaises(ContextReferenceError):
            self.w.send(self.p1['run'], 'case-a', [self.p1['raw']['b']])

    def test_report_detects_a_peer_observation_when_a_route_lets_one_through(self):
        w, s = self.w, self.studio
        leaky = s.register_route({'schemaVersion': V, 'routeId': 'ROUTE-LEAKY', 'version': '1.0', 'patternRef': w.p001,
                                  'protocolRef': w.proto1,
                                  'slotBindings': [{'slotId': sl, 'nodeId': n, 'providerId': w.provider}
                                                   for sl, n in zip(['obsA', 'obsB', 'obsC', 'synth'], [*w.obs.values(), w.human])],
                                  'edges': [{'edgeId': 'leak', 'fromNodeId': SYSTEM_ACTOR_ID, 'toNodeId': w.obs['a'],
                                             'allowedArtifactKinds': ['CASE_INPUT', 'RAW_RESPONSE'], 'order': 1}]})
        run = s.start_run(s.create_trial(w.case, w.ds, 'leaky', w.p001, leaky, w.proto1))
        w.send(run, 'leak', [w.case_input, self.p1['raw']['b']])
        _, violations = pv.independence_report(s, run)
        self.assertEqual(self.codes(violations), {'PEER_OBSERVATION_IN_PACKET'})


class P002OrderTests(StudioTestCase):
    def setUp(self):
        super().setUp()
        self.p2 = self.w.run_p002()

    def test_each_hop_is_a_distinct_transfer(self):
        t = self.p2['transfers']
        ab = self.studio.records.get('transfer', t['a-to-b']).record()
        bc = self.studio.records.get('transfer', t['b-to-c']).record()
        self.assertEqual((ab['fromNodeId'], ab['toNodeId'], ab['artifactIds']), (self.w.obs['a'], self.w.obs['b'], [self.p2['raw']['a']]))
        self.assertEqual((bc['fromNodeId'], bc['toNodeId'], bc['artifactIds']), (self.w.obs['b'], self.w.obs['c'], [self.p2['raw']['b']]))
        tr = pv.trace_artifact(self.studio, self.p2['raw']['b'])
        self.assertEqual((tr.transfer_id, tr.edge_id, tr.problems), (t['a-to-b'], 'a-to-b', []))

    def test_event_order_follows_the_route(self):
        seq = {}
        for e in self.studio.events.read_all()[0]:
            seq.setdefault(e['targetId'], e['sequence'])
        t, raw = self.p2['transfers'], self.p2['raw']
        self.assertLess(seq[raw['a']], seq[t['a-to-b']])
        self.assertLess(seq[t['a-to-b']], seq[raw['b']])
        self.assertLess(seq[raw['b']], seq[t['b-to-c']])
        self.assertLess(seq[t['b-to-c']], seq[raw['c']])

    def test_route_and_transfer_are_different_records(self):
        route = self.studio.records.get('route', 'ROUTE-P002', '1.0').record()
        transfers = [x for x in self.studio.records.records('transfer') if x['runId'] == self.p2['run']]
        self.assertEqual(len(route['edges']), 6)
        self.assertEqual(len(transfers), 6)
        self.assertTrue(all(x['edgeId'] in {e['edgeId'] for e in route['edges']} for x in transfers))
        self.assertFalse(any('transferId' in e for e in route['edges']))
