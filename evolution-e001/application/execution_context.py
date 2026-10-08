"""E001-D execution context: Case / Trial / Run / frozen Run Manifest / Transfer records.

Meaning lives here; persistence is delegated to RecordStore (records), ArtifactStore (RAW
evidence) and EventLog (history). Records are write-once. Current state (is a run still
running?) is derived from events, never written back into a record (TRACEABILITY D-20).

Nothing in this module performs an external action: it records execution context only.
"""
import uuid

from domain.schema.validator import ASSURANCE_RANK, MANUAL_PROVIDER_KINDS
from infrastructure.storage.artifact_store import ArtifactStore, StoreError
from infrastructure.storage.event_log import SCHEMA_VERSION as V, SYSTEM_ACTOR_ID, utc_now
from infrastructure.storage.record_store import RecordNotFound, RecordStore

SYSTEM_ACTOR_RECORD = {
    'schemaVersion': V, 'nodeId': SYSTEM_ACTOR_ID, 'kind': 'SYSTEM', 'displayName': 'Studio',
    'description': 'Reserved E001 system node: the Studio itself as the subject of its own internal operations '
                   '(artifact persistence, event recording, packet dispatch). Not a human, not an AI model, '
                   'not a provider, never a role. See docs/EXECUTION_CONTEXT_E001-D.md.',
}


class ExecutionContextError(Exception):
    pass


class SystemActorConflict(ExecutionContextError):
    pass


class ContextReferenceError(ExecutionContextError):
    """A record names something that does not exist or does not fit."""


class BudgetExceeded(ExecutionContextError):
    pass


class RunNotRunning(ExecutionContextError):
    pass


def new_id(prefix):
    return f'{prefix}-{uuid.uuid4().hex}'


def ref_key(ref):
    return f'{ref["id"]}@{ref["version"]}'


class Studio:
    """One E001 store: records + artifacts + events under a single root directory."""

    def __init__(self, root):
        self.artifacts = ArtifactStore(root)
        self.events = self.artifacts.events
        self.records = RecordStore(root)
        self._ensure_system_actor()

    # ---------- registry records (no event type exists for these; they are anchored at run start) ----------

    def _ensure_system_actor(self):
        if self.records.exists('node', SYSTEM_ACTOR_ID):
            have = self.records.get('node', SYSTEM_ACTOR_ID).record()
            want = dict(SYSTEM_ACTOR_RECORD, createdAt=have.get('createdAt'))
            if have != want:
                raise SystemActorConflict(f'{SYSTEM_ACTOR_ID} exists but is not the reserved system node')
            return
        self.records.put('node', dict(SYSTEM_ACTOR_RECORD, createdAt=utc_now()))

    def register_node(self, kind, display_name, description=None, node_id=None):
        node_id = node_id or new_id('NODE')
        if node_id == SYSTEM_ACTOR_ID:
            raise SystemActorConflict(f'{SYSTEM_ACTOR_ID} is reserved')
        rec = {'schemaVersion': V, 'nodeId': node_id, 'kind': kind, 'displayName': display_name, 'createdAt': utc_now()}
        if description is not None:
            rec['description'] = description
        self.records.put('node', rec)
        return node_id

    def register_provider(self, kind, max_isolation_assurance, description=None, provider_id=None):
        if kind in MANUAL_PROVIDER_KINDS and max_isolation_assurance == 'ENFORCED':
            raise ExecutionContextError('a manual provider cannot claim ENFORCED isolation')
        provider_id = provider_id or new_id('PROV')
        rec = {'schemaVersion': V, 'providerId': provider_id, 'kind': kind,
               'maxIsolationAssurance': max_isolation_assurance, 'createdAt': utc_now()}
        if description is not None:
            rec['description'] = description
        self.records.put('provider', rec)
        return provider_id

    def register_pattern(self, record):
        self.records.put('pattern', record)
        return {'id': record['patternId'], 'version': record['version']}

    def register_protocol(self, record):
        self.records.put('protocol', record)
        return {'id': record['protocolId'], 'version': record['version']}

    def register_route(self, record):
        pattern = self._versioned('pattern', record['patternRef'])
        self._versioned('protocol', record['protocolRef'])
        slots = sorted(s['slotId'] for s in pattern['slots'])
        bound = sorted(b['slotId'] for b in record['slotBindings'])
        if bound != slots:
            raise ContextReferenceError(f'bindings {bound} must cover pattern slots {slots} exactly once')
        bound_nodes = set()
        for b in record['slotBindings']:
            if b['nodeId'] == SYSTEM_ACTOR_ID:
                raise SystemActorConflict(f'{SYSTEM_ACTOR_ID} cannot fill a role slot')
            self._get('node', b['nodeId'])
            self._get('provider', b['providerId'])
            bound_nodes.add(b['nodeId'])
        for e in record['edges']:
            for end in (e['fromNodeId'], e['toNodeId']):
                if end not in bound_nodes and self._get('node', end)['kind'] != 'SYSTEM':
                    raise ContextReferenceError(f'edge {e["edgeId"]}: {end} is neither bound to a slot nor a SYSTEM node')
        if len({e['edgeId'] for e in record['edges']}) != len(record['edges']):
            raise ContextReferenceError('edge ids must be unique within a route')
        self.records.put('route', record)
        return {'id': record['routeId'], 'version': record['version']}

    # ---------- case / design space / source ----------

    def create_case(self, question, intent='', constraints=(), actor=SYSTEM_ACTOR_ID):
        case_id = new_id('CASE')
        stored = self.records.put('case', {'schemaVersion': V, 'caseId': case_id, 'question': question, 'intent': intent,
                                           'constraints': list(constraints), 'createdAt': utc_now(), 'status': 'OPEN'})
        self._event('CASE_CREATED', 'case', case_id, {'recordHash': stored.record_hash}, actor=actor)
        return case_id

    def create_design_space(self, case_id, objective, constraints=(), notes=''):
        self._get('case', case_id)
        ds_id = new_id('DS')
        self.records.put('designSpace', {'schemaVersion': V, 'designSpaceId': ds_id, 'caseId': case_id, 'objective': objective,
                                         'constraints': list(constraints), 'notes': notes, 'createdAt': utc_now()})
        return ds_id

    def register_source(self, case_id, kind, reference, content_artifact_id=None, claimed_hash=None,
                        access_classification='UNKNOWN', actor=SYSTEM_ACTOR_ID):
        """Verification status is derived from evidence only: VERIFIED needs stored bytes; a claim alone stays a claim."""
        self._get('case', case_id)
        verified = None
        if content_artifact_id is not None:
            art = self._artifact(content_artifact_id)
            if art.metadata()['provenance']['caseId'] != case_id:
                raise ContextReferenceError(f'{content_artifact_id} belongs to another case')
            verified = art.content_hash
        if verified is not None:
            status = 'VERIFIED' if claimed_hash in (None, verified) else 'MISMATCH'
        else:
            status = 'CLAIMED_ONLY' if claimed_hash is not None else 'UNVERIFIED'
        source_id = new_id('SRC')
        stored = self.records.put('source', {
            'schemaVersion': V, 'sourceId': source_id, 'caseId': case_id, 'kind': kind, 'reference': reference,
            'contentArtifactId': content_artifact_id, 'claimedHash': claimed_hash, 'verifiedHash': verified,
            'hashAlgorithm': 'SHA-256', 'accessClassification': access_classification,
            'verificationStatus': status, 'createdAt': utc_now()})
        self._event('SOURCE_REGISTERED', 'source', source_id, {'recordHash': stored.record_hash},
                    artifact_refs=[content_artifact_id] if content_artifact_id else [], actor=actor)
        return source_id

    # ---------- trial ----------

    def create_trial(self, case_id, design_space_id, hypothesis, pattern_ref, route_ref, protocol_ref,
                     max_runs=3, previous_trial_id=None, rationale=None, actor=SYSTEM_ACTOR_ID):
        self._get('case', case_id)
        if self._get('designSpace', design_space_id)['caseId'] != case_id:
            raise ContextReferenceError('design space belongs to another case')
        self._versioned('pattern', pattern_ref)
        route = self._versioned('route', route_ref)
        self._versioned('protocol', protocol_ref)
        if route['patternRef'] != pattern_ref or route['protocolRef'] != protocol_ref:
            raise ContextReferenceError('the route is built on a different pattern or protocol than the trial names')
        if previous_trial_id is not None and self._get('trial', previous_trial_id)['caseId'] != case_id:
            raise ContextReferenceError('previous trial belongs to another case')
        trial_id = new_id('TRIAL')
        rec = {'schemaVersion': V, 'trialId': trial_id, 'caseId': case_id, 'designSpaceId': design_space_id,
               'hypothesis': hypothesis, 'patternRef': dict(pattern_ref), 'routeRef': dict(route_ref),
               'protocolRef': dict(protocol_ref), 'appliedExperienceIds': [], 'previousTrialId': previous_trial_id,
               'budget': {'maxRuns': max_runs}, 'createdAt': utc_now(), 'status': 'ACTIVE'}
        if rationale is not None:
            rec['rationale'] = rationale
        stored = self.records.put('trial', rec)
        self._event('TRIAL_CREATED', 'trial', trial_id, {'recordHash': stored.record_hash}, actor=actor)
        return trial_id

    # ---------- run + manifest ----------

    def runs_of_trial(self, trial_id):
        return [r for r in self.records.records('run') if r['trialId'] == trial_id]

    def start_run(self, trial_id, source_ids=(), initial_artifact_ids=(), configuration=None, retry_of_run_id=None,
                  actor=SYSTEM_ACTOR_ID):
        """Freeze the Run Manifest and start the run. Freeze point = run start (D-21): the schema has no
        pre-start run state, and every transfer must find its run's frozen route."""
        trial = self._get('trial', trial_id)
        if len(self.runs_of_trial(trial_id)) >= trial['budget']['maxRuns']:
            raise BudgetExceeded(f'trial budget of {trial["budget"]["maxRuns"]} runs is used up')
        if retry_of_run_id is not None and self._get('run', retry_of_run_id)['trialId'] != trial_id:
            raise ContextReferenceError('a retry must belong to the same trial')
        case_id = trial['caseId']
        hashes = {k: {} for k in ('case', 'trial', 'designSpace', 'pattern', 'route', 'protocol', 'node', 'provider', 'source')}
        hashes['case'][case_id] = self.records.get('case', case_id).record_hash
        hashes['trial'][trial_id] = self.records.get('trial', trial_id).record_hash
        hashes['designSpace'][trial['designSpaceId']] = self.records.get('designSpace', trial['designSpaceId']).record_hash
        pattern = self._versioned('pattern', trial['patternRef'], hashes)
        route = self._versioned('route', trial['routeRef'], hashes)
        self._versioned('protocol', trial['protocolRef'], hashes)
        slot_roles = {s['slotId']: s['role'] for s in pattern['slots']}
        node_ids = [b['nodeId'] for b in route['slotBindings']] + [n for e in route['edges'] for n in (e['fromNodeId'], e['toNodeId'])]
        nodes = []
        for nid in dict.fromkeys(node_ids):
            stored = self.records.get('node', nid)
            hashes['node'][nid] = stored.record_hash
            nodes.append(stored.record())
        providers = []
        for pid in dict.fromkeys(b['providerId'] for b in route['slotBindings']):
            stored = self.records.get('provider', pid)
            hashes['provider'][pid] = stored.record_hash
            providers.append(stored.record())
        sources = []
        for sid in source_ids:
            stored = self.records.get('source', sid)
            src = stored.record()
            if src['caseId'] != case_id:
                raise ContextReferenceError(f'{sid} belongs to another case')
            hashes['source'][sid] = stored.record_hash
            sources.append({k: src[k] for k in ('sourceId', 'verificationStatus', 'verifiedHash', 'claimedHash')})
        initial = {}
        for aid in initial_artifact_ids:
            art = self._artifact(aid)
            if art.metadata()['provenance']['caseId'] != case_id:
                raise ContextReferenceError(f'{aid} belongs to another case')
            initial[aid] = art.content_hash
        run_id, manifest_id = new_id('RUN'), new_id('MAN')
        manifest = self.records.put('runManifest', {
            'schemaVersion': V, 'runManifestId': manifest_id, 'runId': run_id, 'frozenAt': utc_now(),
            'caseId': case_id, 'trialId': trial_id, 'patternRef': trial['patternRef'], 'routeRef': trial['routeRef'],
            'protocolRef': trial['protocolRef'], 'nodes': nodes,
            'roleAssignments': [{'slotId': b['slotId'], 'role': slot_roles[b['slotId']], 'nodeId': b['nodeId'],
                                 'providerId': b['providerId']} for b in route['slotBindings']],
            'providers': providers, 'sources': sources, 'initialArtifactIds': list(initial),
            'configuration': dict(configuration or {})})
        self.records.put('run', {'schemaVersion': V, 'runId': run_id, 'trialId': trial_id, 'runManifestId': manifest_id,
                                 'startedAt': utc_now(), 'completedAt': None, 'status': 'RUNNING',
                                 'retryOfRunId': retry_of_run_id})
        self._event('RUN_STARTED', 'run', run_id,
                    {'manifestHash': manifest.record_hash, 'recordHashes': hashes, 'initialArtifactHashes': initial},
                    artifact_refs=list(initial), actor=actor)
        return run_id

    def run_status(self, run_id):
        """Current state, derived from events: RUNNING / COMPLETED / FAILED, or UNANCHORED when the run
        record exists without its RUN_STARTED event."""
        self._get('run', run_id)
        types = [e['eventType'] for e in self.events.read_all()[0] if e['targetType'] == 'run' and e['targetId'] == run_id]
        if 'RUN_STARTED' not in types:
            return 'UNANCHORED'
        for t, s in (('RUN_FAILED', 'FAILED'), ('RUN_COMPLETED', 'COMPLETED')):
            if t in types:
                return s
        return 'RUNNING'

    def complete_run(self, run_id, actor=SYSTEM_ACTOR_ID):
        self._require_running(run_id)
        self._event('RUN_COMPLETED', 'run', run_id, {}, actor=actor)

    def fail_run(self, run_id, reason, actor=SYSTEM_ACTOR_ID):
        self._require_running(run_id)
        self._event('RUN_FAILED', 'run', run_id, {'reason': reason}, actor=actor)

    def manifest_of(self, run_id):
        return self._get('runManifest', self._get('run', run_id)['runManifestId'])

    # ---------- transfer (record only; no packet assembly, no delivery) ----------

    def record_transfer(self, run_id, edge_id, packet_artifact_id, artifact_ids=(), transfer_id=None,
                        isolation_assurance='PACKET_CONTROLLED', status='HANDED_TO_HUMAN', actor=SYSTEM_ACTOR_ID):
        """Record that a packet moved along one edge of the run's frozen route. Only artifacts that
        already exist can be named, so a transfer can never precede what it carries."""
        self._require_running(run_id)
        manifest = self.manifest_of(run_id)
        route = self._versioned('route', manifest['routeRef'])
        edge = next((e for e in route['edges'] if e['edgeId'] == edge_id), None)
        if edge is None:
            raise ContextReferenceError(f'edge {edge_id!r} is not in the run\'s route')
        binding = next((b for b in route['slotBindings'] if b['nodeId'] == edge['toNodeId']), None)
        if binding is None:
            raise ContextReferenceError(f'destination {edge["toNodeId"]} has no provider binding')
        provider = next(p for p in manifest['providers'] if p['providerId'] == binding['providerId'])
        if ASSURANCE_RANK[isolation_assurance] > ASSURANCE_RANK[provider['maxIsolationAssurance']]:
            raise ExecutionContextError(f'{isolation_assurance} exceeds the provider maximum {provider["maxIsolationAssurance"]}')
        transfer_id = transfer_id or new_id('XFER')
        packet = self._artifact(packet_artifact_id)
        pmeta = packet.metadata()
        if pmeta['kind'] != 'PACKET':
            raise ContextReferenceError(f'{packet_artifact_id} is not a PACKET artifact')
        if (pmeta['provenance']['runId'], pmeta['provenance']['transferId']) != (run_id, transfer_id):
            raise ContextReferenceError('the packet artifact must name this run and this transfer in its provenance')
        for aid in artifact_ids:
            kind = self._artifact(aid).metadata()['kind']
            if kind not in edge['allowedArtifactKinds']:
                raise ContextReferenceError(f'{aid} ({kind}) is not allowed on edge {edge_id}')
        stored = self.records.put('transfer', {
            'schemaVersion': V, 'transferId': transfer_id, 'runId': run_id, 'edgeId': edge_id,
            'fromNodeId': edge['fromNodeId'], 'toNodeId': edge['toNodeId'], 'artifactIds': list(artifact_ids),
            'packetArtifactId': packet_artifact_id, 'packetHash': packet.content_hash, 'method': provider['kind'],
            'createdAt': utc_now(), 'status': status, 'externalDelivery': 'UNKNOWN',
            'isolationAssurance': isolation_assurance})
        self._event('TRANSFER_CREATED', 'transfer', transfer_id,
                    {'recordHash': stored.record_hash, 'packetHash': packet.content_hash},
                    artifact_refs=[packet_artifact_id, *artifact_ids], actor=actor)
        return transfer_id

    # ---------- helpers ----------

    def _require_running(self, run_id):
        status = self.run_status(run_id)
        if status != 'RUNNING':
            raise RunNotRunning(f'{run_id} is {status}')

    def _get(self, entity, record_id):
        try:
            return self.records.get(entity, record_id).record()
        except RecordNotFound:
            raise ContextReferenceError(f'{entity} {record_id!r} does not exist') from None

    def _versioned(self, entity, ref, hashes=None):
        try:
            stored = self.records.get(entity, ref['id'], ref['version'])
        except RecordNotFound:
            raise ContextReferenceError(f'{entity} {ref!r} does not exist') from None
        if hashes is not None:
            hashes[entity][ref_key(ref)] = stored.record_hash
        return stored.record()

    def _artifact(self, artifact_id):
        try:
            art = self.artifacts.read(artifact_id)
        except StoreError as e:
            raise ContextReferenceError(f'artifact {artifact_id!r} is not usable: {e}') from None
        if not art.event_linked:
            raise ContextReferenceError(f'artifact {artifact_id!r} is ORPHAN (no ARTIFACT_RECEIVED event)')
        return art

    def _event(self, event_type, target_type, target_id, payload, artifact_refs=(), actor=SYSTEM_ACTOR_ID):
        self._get('node', actor)
        return self.events.append(event_type=event_type, target_type=target_type, target_id=target_id,
                                  payload=payload, artifact_refs=artifact_refs, actor_node_id=actor)
