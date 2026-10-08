"""Deterministic generator for the E001 schema fixtures.

The committed JSON files are produced by this script and nothing else, so every
hash inside them can be re-derived. tests/unit/test_schema.py checks that
running the generator reproduces the committed bytes exactly.

Run from evolution-e001/:  python -m tests.fixtures.make_fixtures
"""
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
V = 'E001/1.0'

# Unique markers: each appears only in its own observer's RAW response.
# Later isolation tests assert that no P001 packet for another observer contains them.
ISOLATION_SECRETS = {
    'NODE-observer-a': 'SECRET-A',
    'NODE-observer-b': 'SECRET-B',
    'NODE-observer-c': 'SECRET-C',
}


def ts(minute):
    return f'2026-10-08T09:{minute:02d}:00Z'


def sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def artifact(aid, kind, content, minute, source_kind, via, run_id=None, transfer_id=None, producer=None, parents=()):
    return {
        'schemaVersion': V, 'artifactId': aid, 'kind': kind, 'content': content, 'encoding': 'utf-8',
        'byteSize': len(content.encode('utf-8')), 'contentHash': sha(content), 'hashAlgorithm': 'SHA-256',
        'createdAt': ts(minute),
        'provenance': {'sourceKind': source_kind, 'receivedVia': via, 'caseId': 'CASE-FIX-001', 'runId': run_id, 'transferId': transfer_id},
        'producerNodeId': producer, 'parentArtifactIds': list(parents),
    }


def node(nid, kind, name):
    return {'schemaVersion': V, 'nodeId': nid, 'kind': kind, 'displayName': name, 'createdAt': ts(0)}


def finding(text, *obs):
    return {'text': text, 'observationIds': list(obs)}


def build_p001_bundle():
    question = 'Fixture question: what does the attached note say about the harbor?'
    source_text = 'Fixture source note. The harbor opened in 1859.\n'
    run = 'RUN-FIX-P001-1'

    nodes = [
        node('NODE-studio', 'SYSTEM', 'Studio'),
        node('NODE-human-01', 'HUMAN', 'Human operator'),
        node('NODE-observer-a', 'MODEL', 'Observer A'),
        node('NODE-observer-b', 'MODEL', 'Observer B'),
        node('NODE-observer-c', 'MODEL', 'Observer C'),
    ]
    provider = {'schemaVersion': V, 'providerId': 'PROV-manual-clipboard', 'kind': 'MANUAL_CLIPBOARD',
                'maxIsolationAssurance': 'PACKET_CONTROLLED', 'createdAt': ts(0)}

    p001 = {'schemaVersion': V, 'patternId': 'PAT-P001', 'version': '1.0', 'name': 'Independent / Parallel', 'topology': 'PARALLEL',
            'slots': [{'slotId': 'obsA', 'role': 'OBSERVER'}, {'slotId': 'obsB', 'role': 'OBSERVER'},
                      {'slotId': 'obsC', 'role': 'OBSERVER'}, {'slotId': 'synth', 'role': 'SYNTHESIZER'}]}
    p002 = {'schemaVersion': V, 'patternId': 'PAT-P002', 'version': '1.0', 'name': 'Sequential', 'topology': 'SEQUENTIAL',
            'slots': [{'slotId': 'step1', 'role': 'OBSERVER', 'order': 1}, {'slotId': 'step2', 'role': 'OBSERVER', 'order': 2},
                      {'slotId': 'step3', 'role': 'OBSERVER', 'order': 3}, {'slotId': 'synth', 'role': 'SYNTHESIZER', 'order': 4}]}

    case_rule = {'ruleId': 'case-input', 'subject': {'type': 'ANY'}, 'action': 'RECEIVE',
                 'object': {'artifactKinds': ['CASE_INPUT', 'SOURCE_CONTENT'], 'producerRelation': 'NONE'}, 'effect': 'ALLOW', 'condition': 'ALWAYS'}
    submit_rule = {'ruleId': 'observer-submit', 'subject': {'type': 'ROLE', 'value': 'OBSERVER'}, 'action': 'SUBMIT',
                   'object': {'artifactKinds': ['RAW_RESPONSE'], 'producerRelation': 'SELF'}, 'effect': 'ALLOW', 'condition': 'ALWAYS'}
    peer_deny = {'ruleId': 'no-peer', 'subject': {'type': 'ROLE', 'value': 'OBSERVER'}, 'action': 'RECEIVE',
                 'object': {'artifactKinds': ['RAW_RESPONSE'], 'producerRelation': 'PEER'}, 'effect': 'DENY', 'condition': 'ALWAYS'}
    synth_in = {'ruleId': 'synth-input', 'subject': {'type': 'ROLE', 'value': 'SYNTHESIZER'}, 'action': 'RECEIVE',
                'object': {'artifactKinds': ['RAW_RESPONSE'], 'producerRelation': 'ROUTE_PREDECESSOR'}, 'effect': 'ALLOW', 'condition': 'ROUTE_EDGE_EXISTS'}
    proto1 = {'schemaVersion': V, 'protocolId': 'PROTO-P001', 'version': '1.0', 'defaultEffect': 'DENY',
              'conflictResolution': 'DENY_OVERRIDES', 'violationAction': 'BLOCK', 'rules': [case_rule, submit_rule, peer_deny, synth_in]}
    pred_allow = {'ruleId': 'predecessor', 'subject': {'type': 'ROLE', 'value': 'OBSERVER'}, 'action': 'RECEIVE',
                  'object': {'artifactKinds': ['RAW_RESPONSE'], 'producerRelation': 'ROUTE_PREDECESSOR'}, 'effect': 'ALLOW', 'condition': 'ROUTE_EDGE_EXISTS'}
    proto2 = {'schemaVersion': V, 'protocolId': 'PROTO-P002', 'version': '1.0', 'defaultEffect': 'DENY',
              'conflictResolution': 'DENY_OVERRIDES', 'violationAction': 'BLOCK', 'rules': [case_rule, submit_rule, pred_allow, peer_deny, synth_in]}

    obs_nodes = ['NODE-observer-a', 'NODE-observer-b', 'NODE-observer-c']
    case_kinds = ['CASE_INPUT', 'SOURCE_CONTENT']
    route1 = {'schemaVersion': V, 'routeId': 'ROUTE-P001-FIX', 'version': '1.0',
              'patternRef': {'id': 'PAT-P001', 'version': '1.0'}, 'protocolRef': {'id': 'PROTO-P001', 'version': '1.0'},
              'slotBindings': [{'slotId': s, 'nodeId': n, 'providerId': 'PROV-manual-clipboard'}
                               for s, n in zip(['obsA', 'obsB', 'obsC', 'synth'], obs_nodes + ['NODE-human-01'])],
              'edges': [{'edgeId': f'case-{x}', 'fromNodeId': 'NODE-studio', 'toNodeId': n, 'allowedArtifactKinds': case_kinds, 'order': 1}
                        for x, n in zip('abc', obs_nodes)]
                       + [{'edgeId': f'syn-{x}', 'fromNodeId': n, 'toNodeId': 'NODE-human-01', 'allowedArtifactKinds': ['RAW_RESPONSE'], 'order': 2}
                          for x, n in zip('abc', obs_nodes)]}
    route2 = {'schemaVersion': V, 'routeId': 'ROUTE-P002-FIX', 'version': '1.0',
              'patternRef': {'id': 'PAT-P002', 'version': '1.0'}, 'protocolRef': {'id': 'PROTO-P002', 'version': '1.0'},
              'slotBindings': [{'slotId': s, 'nodeId': n, 'providerId': 'PROV-manual-clipboard'}
                               for s, n in zip(['step1', 'step2', 'step3', 'synth'], obs_nodes + ['NODE-human-01'])],
              'edges': [{'edgeId': f'case-{x}', 'fromNodeId': 'NODE-studio', 'toNodeId': n, 'allowedArtifactKinds': case_kinds, 'order': i}
                        for i, (x, n) in enumerate(zip('abc', obs_nodes), 1)]
                       + [{'edgeId': 'a-to-b', 'fromNodeId': 'NODE-observer-a', 'toNodeId': 'NODE-observer-b', 'allowedArtifactKinds': ['RAW_RESPONSE'], 'order': 2},
                          {'edgeId': 'b-to-c', 'fromNodeId': 'NODE-observer-b', 'toNodeId': 'NODE-observer-c', 'allowedArtifactKinds': ['RAW_RESPONSE'], 'order': 3},
                          {'edgeId': 'c-to-syn', 'fromNodeId': 'NODE-observer-c', 'toNodeId': 'NODE-human-01', 'allowedArtifactKinds': ['RAW_RESPONSE'], 'order': 4}]}

    case = {'schemaVersion': V, 'caseId': 'CASE-FIX-001', 'question': question, 'intent': 'Schema fixture only. Not a research case.',
            'constraints': ['Do not infer beyond the source.'], 'createdAt': ts(0), 'status': 'OPEN'}
    ds = {'schemaVersion': V, 'designSpaceId': 'DS-FIX-001', 'caseId': 'CASE-FIX-001', 'objective': question,
          'constraints': [], 'notes': 'Compare P001 and P002 on the same case.', 'createdAt': ts(1)}

    def trial(tid, pat, route, proto, hyp):
        return {'schemaVersion': V, 'trialId': tid, 'caseId': 'CASE-FIX-001', 'designSpaceId': 'DS-FIX-001', 'hypothesis': hyp,
                'patternRef': {'id': pat, 'version': '1.0'}, 'routeRef': {'id': route, 'version': '1.0'},
                'protocolRef': {'id': proto, 'version': '1.0'}, 'appliedExperienceIds': [], 'previousTrialId': None,
                'budget': {'maxRuns': 3}, 'createdAt': ts(2), 'status': 'ACTIVE'}
    t1 = trial('TRIAL-FIX-P001', 'PAT-P001', 'ROUTE-P001-FIX', 'PROTO-P001', 'Independent observers expose differences.')
    t2 = trial('TRIAL-FIX-P002', 'PAT-P002', 'ROUTE-P002-FIX', 'PROTO-P002', 'Sequential observers build on each other.')
    t2['previousTrialId'] = 'TRIAL-FIX-P001'
    t2['status'] = 'PLANNED'

    a_case = artifact('ART-case-input', 'CASE_INPUT', question, 0, 'USER', 'SYSTEM_GENERATED')
    a_src = artifact('ART-source-001', 'SOURCE_CONTENT', source_text, 1, 'USER', 'MANUAL_FILE')
    source = {'schemaVersion': V, 'sourceId': 'SRC-FIX-001', 'caseId': 'CASE-FIX-001', 'kind': 'TEXT', 'reference': 'fixture-note.txt',
              'contentArtifactId': 'ART-source-001', 'claimedHash': None, 'verifiedHash': sha(source_text), 'hashAlgorithm': 'SHA-256',
              'accessClassification': 'PUBLIC', 'verificationStatus': 'VERIFIED', 'createdAt': ts(1)}

    packets, raws, transfers, observations = [], [], [], []
    raw_text = {
        'a': 'Observation A: the note says the harbor opened in 1859. SECRET-A',
        'b': 'Observation B: opening year 1859 per the note.\r\nUnclear who opened it. SECRET-B',
        'c': 'Observation C: the note gives 1858 as the opening year. SECRET-C',
    }
    for i, (x, n) in enumerate(zip('abc', obs_nodes)):
        xfer = f'XFER-fix-{x}'
        pkt_text = f'[packet to {n}]\nQUESTION:\n{question}\nSOURCE SRC-FIX-001:\n{source_text}'
        pkt = artifact(f'ART-packet-{x}', 'PACKET', pkt_text, 5, 'SYSTEM', 'SYSTEM_GENERATED', run, xfer, 'NODE-studio', ['ART-case-input', 'ART-source-001'])
        raw = artifact(f'ART-raw-{x}', 'RAW_RESPONSE', raw_text[x], 10 + i, 'AI', 'MANUAL_PASTE', run, xfer, n, [f'ART-packet-{x}'])
        packets.append(pkt)
        raws.append(raw)
        transfers.append({'schemaVersion': V, 'transferId': xfer, 'runId': run, 'edgeId': f'case-{x}', 'fromNodeId': 'NODE-studio', 'toNodeId': n,
                          'artifactIds': ['ART-case-input', 'ART-source-001'], 'packetArtifactId': pkt['artifactId'], 'packetHash': pkt['contentHash'],
                          'method': 'MANUAL_CLIPBOARD', 'createdAt': ts(5), 'status': 'RESPONSE_RECEIVED', 'externalDelivery': 'UNKNOWN',
                          'isolationAssurance': 'PACKET_CONTROLLED'})
        observations.append({'schemaVersion': V, 'observationId': f'OBS-fix-{x}', 'runId': run, 'transferId': xfer, 'observerNodeId': n,
                             'providerId': 'PROV-manual-clipboard', 'inputArtifactIds': ['ART-case-input', 'ART-source-001'],
                             'rawResponseArtifactId': raw['artifactId'], 'parseStatus': 'UNPARSED', 'fields': {}, 'createdAt': ts(15)})

    manifest = {'schemaVersion': V, 'runManifestId': 'MAN-FIX-P001-1', 'runId': run, 'frozenAt': ts(4), 'caseId': 'CASE-FIX-001',
                'trialId': 'TRIAL-FIX-P001', 'patternRef': {'id': 'PAT-P001', 'version': '1.0'}, 'routeRef': {'id': 'ROUTE-P001-FIX', 'version': '1.0'},
                'protocolRef': {'id': 'PROTO-P001', 'version': '1.0'}, 'nodes': nodes,
                'roleAssignments': [{'slotId': b['slotId'], 'role': 'SYNTHESIZER' if b['slotId'] == 'synth' else 'OBSERVER',
                                     'nodeId': b['nodeId'], 'providerId': b['providerId']} for b in route1['slotBindings']],
                'providers': [provider], 'sources': [{'sourceId': 'SRC-FIX-001', 'verificationStatus': 'VERIFIED',
                                                      'verifiedHash': sha(source_text), 'claimedHash': None}],
                'initialArtifactIds': ['ART-case-input', 'ART-source-001'], 'configuration': {}}
    run_rec = {'schemaVersion': V, 'runId': run, 'trialId': 'TRIAL-FIX-P001', 'runManifestId': 'MAN-FIX-P001-1',
               'startedAt': ts(4), 'completedAt': None, 'status': 'PAUSED', 'retryOfRunId': None}

    synthesis = {'schemaVersion': V, 'synthesisId': 'SYN-FIX-001', 'runId': run, 'synthesizerNodeId': 'NODE-human-01',
                 'inputObservationIds': ['OBS-fix-a', 'OBS-fix-b', 'OBS-fix-c'], 'rawResponseArtifactId': None,
                 'common': [finding('All observers report an opening year from the note.', 'OBS-fix-a', 'OBS-fix-b', 'OBS-fix-c')],
                 'differences': [], 'unknowns': [finding('Who opened the harbor.', 'OBS-fix-b')],
                 'contradictions': [finding('1859 (A, B) vs 1858 (C). Not resolved by count.', 'OBS-fix-a', 'OBS-fix-b', 'OBS-fix-c')],
                 'executionNeeds': [], 'nextQuestions': [finding('Re-read the source line for the year.', 'OBS-fix-c')], 'createdAt': ts(20)}
    gate = {'schemaVersion': V, 'gateId': 'GATE-FIX-001', 'targetType': 'synthesis', 'targetId': 'SYN-FIX-001', 'targetContentHash': None,
            'actionClass': 'DECISION', 'proposedScope': 'Accept the synthesis of CASE-FIX-001 run 1 as the working summary.',
            'status': 'HOLD', 'decidedBy': 'NODE-human-01', 'decidedAt': ts(25), 'reason': 'Contradiction on the year is unresolved.'}
    evaluation = {'schemaVersion': V, 'evaluationId': 'EVAL-FIX-001', 'targetType': 'artifact', 'targetId': 'ART-raw-a', 'level': 'L1',
                  'criterion': 'HASH_MATCH', 'status': 'PASS', 'evaluatorNodeId': 'NODE-studio',
                  'evidence': 'SHA-256 of stored bytes equals contentHash.', 'createdAt': ts(21)}

    ev_specs = [('CASE_CREATED', 'case', 'CASE-FIX-001', [], 0), ('SOURCE_REGISTERED', 'source', 'SRC-FIX-001', ['ART-source-001'], 1),
                ('TRIAL_CREATED', 'trial', 'TRIAL-FIX-P001', [], 2), ('TRIAL_CREATED', 'trial', 'TRIAL-FIX-P002', [], 2),
                ('RUN_STARTED', 'run', run, [], 4)]
    ev_specs += [('PACKET_ASSEMBLED', 'artifact', p['artifactId'], [p['artifactId']], 5) for p in packets]
    ev_specs += [('TRANSFER_CREATED', 'transfer', t['transferId'], [t['packetArtifactId']], 5) for t in transfers]
    ev_specs += [('ARTIFACT_RECEIVED', 'artifact', r['artifactId'], [r['artifactId']], 10 + i) for i, r in enumerate(raws)]
    ev_specs += [('OBSERVATION_CREATED', 'observation', o['observationId'], [o['rawResponseArtifactId']], 15) for o in observations]
    ev_specs += [('SYNTHESIS_CREATED', 'synthesis', 'SYN-FIX-001', [], 20), ('EVALUATION_CREATED', 'evaluation', 'EVAL-FIX-001', ['ART-raw-a'], 21),
                 ('GATE_DECIDED', 'gate', 'GATE-FIX-001', [], 25)]
    events = [{'schemaVersion': V, 'eventId': f'EVT-fix-{i:03d}', 'sequence': i, 'timestamp': ts(minute),
               'actorNodeId': 'NODE-human-01' if etype in ('CASE_CREATED', 'GATE_DECIDED', 'SYNTHESIS_CREATED') else 'NODE-studio',
               'eventType': etype, 'targetType': ttype, 'targetId': tid, 'payload': {}, 'artifactRefs': refs}
              for i, (etype, ttype, tid, refs, minute) in enumerate(ev_specs, 1)]

    return {
        'schemaVersion': V, 'bundleType': 'E001_STORE_BUNDLE', 'createdAt': ts(30),
        'records': {
            'case': [case], 'designSpace': [ds], 'node': nodes, 'provider': [provider],
            'pattern': [p001, p002], 'route': [route1, route2], 'protocol': [proto1, proto2],
            'trial': [t1, t2], 'run': [run_rec], 'runManifest': [manifest], 'source': [source],
            'artifact': [a_case, a_src] + packets + raws, 'event': events, 'transfer': transfers,
            'observation': observations, 'synthesis': [synthesis], 'gate': [gate], 'evaluation': [evaluation],
        },
    }


def render(obj):
    return (json.dumps(obj, ensure_ascii=False, indent=2) + '\n').encode('utf-8')


FIXTURES = {
    'p001_bundle_valid.json': build_p001_bundle,
    'isolation_secrets.json': lambda: ISOLATION_SECRETS,
}


def main():
    for name, build in FIXTURES.items():
        (HERE / name).write_bytes(render(build()))
        print('wrote', name)


if __name__ == '__main__':
    main()
