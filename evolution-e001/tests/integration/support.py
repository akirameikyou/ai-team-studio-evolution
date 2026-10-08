"""Shared E001-D scenario: one case run as P001 (independent) and P002 (sequential) on the same core.

Packets here are written by hand: packet assembly is E001-F. What is under test is that the
records of who received what, along which edge, in which run, are kept and can be traced.
"""
import tempfile
import unittest
from pathlib import Path

from application.execution_context import Studio, new_id
from infrastructure.storage.event_log import SCHEMA_VERSION as V, SYSTEM_ACTOR_ID

SECRETS = {'a': 'SECRET-A', 'b': 'SECRET-B', 'c': 'SECRET-C'}
CASE_KINDS = ['CASE_INPUT', 'SOURCE_CONTENT']


def rule(rule_id, subject, action, kinds, relation, effect, condition='ALWAYS'):
    return {'ruleId': rule_id, 'subject': subject, 'action': action,
            'object': {'artifactKinds': kinds, 'producerRelation': relation}, 'effect': effect, 'condition': condition}


class World:
    """Registry records + one case. Attributes hold the ids."""

    def __init__(self, studio):
        s = self.studio = studio
        self.obs = {x: s.register_node('MODEL', f'Observer {x.upper()}') for x in 'abc'}
        self.human = s.register_node('HUMAN', 'Human operator')
        self.provider = s.register_provider('MANUAL_CLIPBOARD', 'PACKET_CONTROLLED')
        self.p001 = s.register_pattern({'schemaVersion': V, 'patternId': 'PAT-P001', 'version': '1.0', 'name': 'Independent',
                                        'topology': 'PARALLEL', 'slots': [{'slotId': f'obs{x.upper()}', 'role': 'OBSERVER'} for x in 'abc']
                                        + [{'slotId': 'synth', 'role': 'SYNTHESIZER'}]})
        self.p002 = s.register_pattern({'schemaVersion': V, 'patternId': 'PAT-P002', 'version': '1.0', 'name': 'Sequential',
                                        'topology': 'SEQUENTIAL', 'slots': [{'slotId': f'step{i}', 'role': 'OBSERVER', 'order': i} for i in (1, 2, 3)]
                                        + [{'slotId': 'synth', 'role': 'SYNTHESIZER', 'order': 4}]})
        case_in = rule('case-input', {'type': 'ANY'}, 'RECEIVE', CASE_KINDS, 'NONE', 'ALLOW')
        no_peer = rule('no-peer', {'type': 'ROLE', 'value': 'OBSERVER'}, 'RECEIVE', ['RAW_RESPONSE'], 'PEER', 'DENY')
        pred = rule('predecessor', {'type': 'ROLE', 'value': 'OBSERVER'}, 'RECEIVE', ['RAW_RESPONSE'], 'ROUTE_PREDECESSOR', 'ALLOW', 'ROUTE_EDGE_EXISTS')
        synth = rule('synth-input', {'type': 'ROLE', 'value': 'SYNTHESIZER'}, 'RECEIVE', ['RAW_RESPONSE'], 'ROUTE_PREDECESSOR', 'ALLOW', 'ROUTE_EDGE_EXISTS')
        proto = lambda pid, rules: {'schemaVersion': V, 'protocolId': pid, 'version': '1.0', 'defaultEffect': 'DENY',
                                    'conflictResolution': 'DENY_OVERRIDES', 'violationAction': 'BLOCK', 'rules': rules}
        self.proto1 = s.register_protocol(proto('PROTO-P001', [case_in, no_peer, synth]))
        self.proto2 = s.register_protocol(proto('PROTO-P002', [case_in, pred, no_peer, synth]))
        binds = lambda slots: [{'slotId': sl, 'nodeId': n, 'providerId': self.provider}
                               for sl, n in zip(slots, [self.obs[x] for x in 'abc'] + [self.human])]
        case_edges = [{'edgeId': f'case-{x}', 'fromNodeId': SYSTEM_ACTOR_ID, 'toNodeId': self.obs[x],
                       'allowedArtifactKinds': CASE_KINDS, 'order': 1} for x in 'abc']
        self.route1 = s.register_route({'schemaVersion': V, 'routeId': 'ROUTE-P001', 'version': '1.0', 'patternRef': self.p001,
                                        'protocolRef': self.proto1, 'slotBindings': binds(['obsA', 'obsB', 'obsC', 'synth']),
                                        'edges': case_edges + [{'edgeId': f'syn-{x}', 'fromNodeId': self.obs[x], 'toNodeId': self.human,
                                                                'allowedArtifactKinds': ['RAW_RESPONSE'], 'order': 2} for x in 'abc']})
        self.route2 = s.register_route({'schemaVersion': V, 'routeId': 'ROUTE-P002', 'version': '1.0', 'patternRef': self.p002,
                                        'protocolRef': self.proto2, 'slotBindings': binds(['step1', 'step2', 'step3', 'synth']),
                                        'edges': case_edges + [
                                            {'edgeId': 'a-to-b', 'fromNodeId': self.obs['a'], 'toNodeId': self.obs['b'], 'allowedArtifactKinds': ['RAW_RESPONSE'], 'order': 2},
                                            {'edgeId': 'b-to-c', 'fromNodeId': self.obs['b'], 'toNodeId': self.obs['c'], 'allowedArtifactKinds': ['RAW_RESPONSE'], 'order': 3},
                                            {'edgeId': 'c-to-syn', 'fromNodeId': self.obs['c'], 'toNodeId': self.human, 'allowedArtifactKinds': ['RAW_RESPONSE'], 'order': 4}]})
        self.question = 'What year did the harbor open?'
        self.case = s.create_case(self.question, intent='E001-D test case')
        self.ds = s.create_design_space(self.case, 'Compare P001 and P002 on the same case')
        self.case_input = s.artifacts.create(content=self.question.encode(), kind='CASE_INPUT', case_id=self.case,
                                             source_kind='USER', received_via='SYSTEM_GENERATED').artifact_id
        self.source_text = 'The harbor opened in 1859.\r\n'
        self.source_artifact = s.artifacts.create(content=self.source_text.encode(), kind='SOURCE_CONTENT', case_id=self.case,
                                                  source_kind='USER', received_via='MANUAL_FILE').artifact_id
        self.source = s.register_source(self.case, 'TEXT', 'harbor-note.txt', content_artifact_id=self.source_artifact,
                                        access_classification='PUBLIC')

    def trial(self, pattern='P001', **kw):
        p, r, pr = (self.p001, self.route1, self.proto1) if pattern == 'P001' else (self.p002, self.route2, self.proto2)
        return self.studio.create_trial(self.case, self.ds, f'{pattern} hypothesis', p, r, pr, **kw)

    def start(self, trial_id, **kw):
        return self.studio.start_run(trial_id, source_ids=[self.source], initial_artifact_ids=[self.case_input, self.source_artifact],
                                     configuration={'contextAssembly': 'manual-test'}, **kw)

    def send(self, run_id, edge_id, carried, text=None, transfer_id=None):
        """Write a hand-made packet artifact and record its transfer. Returns (transfer_id, packet_id)."""
        s, tid = self.studio, transfer_id or new_id('XFER')
        body = text if text is not None else f'[{edge_id}]\n' + '\n'.join(s.artifacts.read(a).text() for a in carried)
        pkt = s.artifacts.create(content=body.encode(), kind='PACKET', case_id=self.case, source_kind='SYSTEM',
                                 received_via='SYSTEM_GENERATED', run_id=run_id, transfer_id=tid, producer_node_id=SYSTEM_ACTOR_ID,
                                 parent_artifact_ids=carried).artifact_id
        s.record_transfer(run_id, edge_id, pkt, artifact_ids=carried, transfer_id=tid)
        return tid, pkt

    def respond(self, run_id, transfer_id, node_id, text):
        return self.studio.artifacts.create(content=text.encode(), kind='RAW_RESPONSE', case_id=self.case, source_kind='AI',
                                            received_via='MANUAL_PASTE', run_id=run_id, transfer_id=transfer_id,
                                            producer_node_id=node_id).artifact_id

    def run_p001(self):
        trial = self.trial('P001')
        run = self.start(trial)
        out = {'trial': trial, 'run': run, 'transfers': {}, 'raw': {}, 'packets': {}}
        for x in 'abc':
            tid, pkt = self.send(run, f'case-{x}', [self.case_input, self.source_artifact])
            out['transfers'][x], out['packets'][x] = tid, pkt
            out['raw'][x] = self.respond(run, tid, self.obs[x], f'Observer {x.upper()}: 1859. {SECRETS[x]}\r\n')
        for x in 'abc':
            out['transfers'][f'syn-{x}'] = self.send(run, f'syn-{x}', [out['raw'][x]])[0]
        return out

    def run_p002(self):
        trial = self.trial('P002')
        run = self.start(trial)
        out = {'trial': trial, 'run': run, 'transfers': {}, 'raw': {}}
        prev = None
        for x, edge in zip('abc', (None, 'a-to-b', 'b-to-c')):
            tid, _ = self.send(run, f'case-{x}', [self.case_input, self.source_artifact])
            out['transfers'][f'case-{x}'] = tid
            if edge:
                tid, _ = self.send(run, edge, [prev])
                out['transfers'][edge] = tid
            prev = out['raw'][x] = self.respond(run, tid, self.obs[x], f'Observer {x.upper()} builds on what it saw. {SECRETS[x]}')
        out['transfers']['c-to-syn'] = self.send(run, 'c-to-syn', [prev])[0]
        return out


class StudioTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.studio = Studio(self.tmp / 'store')
        self.w = World(self.studio)

    def tearDown(self):
        self._tmp.cleanup()

    def codes(self, problems):
        return {p.code for p in problems}
