"""E001-D provenance: trace Artifact -> Transfer -> Run -> Trial -> Case and back, check that a
run can be reconstructed from its frozen manifest, and report P001 independence from records.

Everything here only reads. Links are followed by ID; nothing is copied to make a trace work.
"""
import dataclasses

from application.execution_context import ref_key
from domain.schema import validator
from infrastructure.storage.artifact_store import StoreError
from infrastructure.storage.event_log import SYSTEM_ACTOR_ID, Problem
from infrastructure.storage.record_store import RecordStoreError

V = 'E001/1.0'


@dataclasses.dataclass
class ArtifactTrace:
    artifact_id: str
    kind: str
    producer_node_id: str
    transfer_id: str = None
    edge_id: str = None
    from_node_id: str = None
    to_node_id: str = None
    run_id: str = None
    trial_id: str = None
    case_id: str = None
    role: str = None        # role of the producer in the run, from the frozen manifest
    provider_id: str = None
    pattern_ref: dict = None
    route_ref: dict = None
    protocol_ref: dict = None
    problems: list = dataclasses.field(default_factory=list)


def _events(studio):
    return studio.events.read_all()[0]


def _run_started(studio, run_id):
    return next((e for e in _events(studio) if e['eventType'] == 'RUN_STARTED' and e['targetId'] == run_id), None)


def trace_artifact(studio, artifact_id):
    """Upward trace. Every link is checked against the next one; disagreements become problems."""
    meta = studio.artifacts.read(artifact_id).metadata()
    prov = meta['provenance']
    tr = ArtifactTrace(artifact_id, meta['kind'], meta['producerNodeId'], case_id=prov['caseId'])

    def bad(code, message):
        tr.problems.append(Problem(code, artifact_id, message))

    run_id = prov['runId']
    if prov['transferId'] is not None:
        try:
            t = studio.records.get('transfer', prov['transferId']).record()
        except RecordStoreError as e:
            bad('BROKEN_LINK', f'transfer {prov["transferId"]}: {e}')
            return tr
        tr.transfer_id, tr.edge_id, tr.from_node_id, tr.to_node_id = t['transferId'], t['edgeId'], t['fromNodeId'], t['toNodeId']
        if t['runId'] != run_id:
            bad('PROVENANCE_MISMATCH', 'artifact names a different run than its transfer')
        run_id = t['runId']
        # A packet is assembled and dispatched by the Studio itself, whatever node the edge starts at
        # (in P002 the edge A->B carries A's output, but the packet is still built by the Studio).
        # A response is produced by the node the packet was delivered to.
        expected_producer = SYSTEM_ACTOR_ID if meta['kind'] == 'PACKET' else t['toNodeId']
        if meta['producerNodeId'] != expected_producer:
            bad('PROVENANCE_MISMATCH', f'producer {meta["producerNodeId"]} should be {expected_producer} for a {meta["kind"]}')
    if run_id is None:
        return tr  # case-level artifact (case input, source content)
    try:
        run = studio.records.get('run', run_id).record()
        man = studio.records.get('runManifest', run['runManifestId']).record()
        trial = studio.records.get('trial', run['trialId']).record()
    except RecordStoreError as e:
        bad('BROKEN_LINK', str(e))
        return tr
    tr.run_id, tr.trial_id = run_id, run['trialId']
    tr.pattern_ref, tr.route_ref, tr.protocol_ref = man['patternRef'], man['routeRef'], man['protocolRef']
    if trial['caseId'] != prov['caseId']:
        bad('PROVENANCE_MISMATCH', 'artifact names a different case than its run\'s trial')
    assignment = next((a for a in man['roleAssignments'] if a['nodeId'] == meta['producerNodeId']), None)
    if assignment:
        tr.role, tr.provider_id = assignment['role'], assignment['providerId']
    return tr


def trace_case(studio, case_id):
    """Downward trace built from references (reverse lookups over stored records)."""
    studio.records.get('case', case_id)
    artifacts = []
    for aid in studio.artifacts.list_ids():
        try:
            artifacts.append(studio.artifacts.read(aid).metadata())
        except StoreError:
            continue  # reported by verify_store
    transfers = studio.records.records('transfer')
    runs = studio.records.records('run')
    out = {'caseId': case_id, 'trials': []}
    for trial in (t for t in studio.records.records('trial') if t['caseId'] == case_id):
        t_node = {'trialId': trial['trialId'], 'runs': []}
        for run in (r for r in runs if r['trialId'] == trial['trialId']):
            r_node = {'runId': run['runId'], 'status': studio.run_status(run['runId']), 'transfers': []}
            for x in (x for x in transfers if x['runId'] == run['runId']):
                responses = [a['artifactId'] for a in artifacts
                             if a['provenance']['transferId'] == x['transferId'] and a['kind'] != 'PACKET']
                r_node['transfers'].append({'transferId': x['transferId'], 'edgeId': x['edgeId'],
                                            'from': x['fromNodeId'], 'to': x['toNodeId'],
                                            'packetArtifactId': x['packetArtifactId'], 'carried': x['artifactIds'],
                                            'responses': responses})
            t_node['runs'].append(r_node)
        out['trials'].append(t_node)
    return out


def reconstruct_run(studio, run_id):
    """Check that a run can be rebuilt from its frozen manifest: the manifest and every record it
    froze still hash to the values recorded in RUN_STARTED, and every transfer and artifact of the
    run verifies. Returns (summary, problems); no problems means reconstructable."""
    problems = []

    def bad(code, message):
        problems.append(Problem(code, run_id, message))

    started = _run_started(studio, run_id)
    if started is None:
        bad('UNANCHORED_RUN', 'no RUN_STARTED event')
        return None, problems
    p = started['payload']
    try:
        run = studio.records.get('run', run_id).record()
        man_stored = studio.records.get('runManifest', run['runManifestId'])
    except RecordStoreError as e:
        bad('BROKEN_LINK', str(e))
        return None, problems
    if man_stored.record_hash != p.get('manifestHash'):
        bad('MANIFEST_MISMATCH', 'manifest differs from the one frozen at run start')
    for entity, frozen in (p.get('recordHashes') or {}).items():
        for key, h in frozen.items():
            rid, _, ver = key.partition('@')
            try:
                now = studio.records.get(entity, rid, ver or None).record_hash
            except RecordStoreError as e:
                bad('FROZEN_RECORD_MISSING', f'{entity} {key}: {e}')
                continue
            if now != h:
                bad('FROZEN_RECORD_MISMATCH', f'{entity} {key} changed after the run started')
    for aid, h in (p.get('initialArtifactHashes') or {}).items():
        try:
            if studio.artifacts.read(aid).content_hash != h:
                bad('FROZEN_ARTIFACT_MISMATCH', aid)
        except StoreError as e:
            bad('FROZEN_ARTIFACT_MISMATCH', f'{aid}: {e}')
    man = man_stored.record()
    transfers = [t for t in studio.records.records('transfer') if t['runId'] == run_id]
    for t in transfers:
        for aid in [t['packetArtifactId'], *t['artifactIds']]:
            try:
                studio.artifacts.read(aid)
            except StoreError as e:
                bad('ARTIFACT_UNVERIFIED', f'{t["transferId"]} -> {aid}: {e}')
    summary = {'runId': run_id, 'manifestId': man['runManifestId'], 'patternRef': man['patternRef'],
               'routeRef': man['routeRef'], 'protocolRef': man['protocolRef'],
               'roles': {a['nodeId']: a['role'] for a in man['roleAssignments']},
               'transfers': [t['transferId'] for t in transfers]}
    return summary, problems


def independence_report(studio, run_id):
    """P001 check from records: for each transfer to an OBSERVER, which artifacts were carried, and did
    any of them come from a peer observer of the same run. Detection only; enforcement is E001-F."""
    man = studio.manifest_of(run_id)
    roles = {a['nodeId']: a['role'] for a in man['roleAssignments']}
    observers = {n for n, r in roles.items() if r == 'OBSERVER'}
    entries, violations = [], []
    for t in (t for t in studio.records.records('transfer') if t['runId'] == run_id and t['toNodeId'] in observers):
        peer = []
        for aid in t['artifactIds']:
            meta = studio.artifacts.read(aid).metadata()
            if meta['kind'] == 'RAW_RESPONSE' and meta['producerNodeId'] in observers and meta['producerNodeId'] != t['toNodeId']:
                peer.append(aid)
        entries.append({'transferId': t['transferId'], 'observer': t['toNodeId'], 'carried': t['artifactIds'],
                        'packetHash': t['packetHash'], 'isolationAssurance': t['isolationAssurance'], 'peerArtifacts': peer})
        if peer:
            violations.append(Problem('PEER_OBSERVATION_IN_PACKET', t['transferId'], f'{t["toNodeId"]} received {peer}'))
    return entries, violations


def build_bundle(studio):
    records = {entity: studio.records.records(entity) for entity in
               ('case', 'designSpace', 'node', 'provider', 'pattern', 'route', 'protocol', 'trial', 'run', 'runManifest',
                'source', 'transfer')}
    records.update(studio.artifacts.bundle_records())
    return {'schemaVersion': V, 'bundleType': 'E001_STORE_BUNDLE', 'records': records}


def verify_store(studio):
    """Whole-store check: artifacts, records, record anchors in events, run reconstruction, and the
    E001 bundle rules (SR-01..SR-21). An empty list means everything agrees."""
    problems = list(studio.artifacts.verify()) + list(studio.records.verify())
    if problems:
        return problems  # stop before interpreting records that do not read back cleanly
    events = _events(studio)
    anchored = {}
    for e in events:
        if 'recordHash' in e['payload']:
            anchored[(e['targetType'], e['targetId'])] = e['payload']['recordHash']
    for entity in ('case', 'trial', 'source', 'transfer'):
        for rid in studio.records.list(entity):
            h = anchored.get((entity, rid))
            if h is None:
                problems.append(Problem('UNANCHORED_RECORD', f'{entity}/{rid}', 'no creation event records this record'))
            elif studio.records.get(entity, rid).record_hash != h:
                problems.append(Problem('RECORD_MISMATCH', f'{entity}/{rid}', 'record differs from the hash in its creation event'))
    run_manifests = {r['runManifestId'] for r in studio.records.records('run')}
    for mid in studio.records.list('runManifest'):
        if mid not in run_manifests:
            problems.append(Problem('ORPHAN_MANIFEST', f'runManifest/{mid}', 'no run record points to this manifest'))
    for rid in studio.records.list('run'):
        problems += reconstruct_run(studio, rid)[1]
    for issue in validator.validate_bundle(build_bundle(studio)):
        problems.append(Problem(f'BUNDLE_{issue.code}', issue.path, f'{issue.rule} {issue.message}'.strip()))
    return problems
