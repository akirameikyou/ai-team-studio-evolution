"""E001-F1/U3-1: run context and artifact candidate inventory (read only).

For one Run, read the stored records and build:
  * the RunContext that U1's evaluate_receive expects, from the frozen Manifest and Route;
  * an inventory of the artifacts that belong to the Run's Case, each classified and, where it
    claims to belong to this Run, checked for provenance consistency.

Nothing is decided here: no artifact is accepted or rejected for a packet (that is U3-2), no
packet is built (U3-3) and nothing is written - no artifact, record, event or transfer.
Missing or broken data is never skipped silently: it stops the inventory (ContextAssemblyError)
or is listed with its reason.
"""
from types import MappingProxyType
from typing import NamedTuple

from application.execution_context import ContextReferenceError
from application.provenance import reconstruct_run, trace_artifact
from domain.protocol.evaluator import RunContext
from infrastructure.storage.artifact_store import StoreError
from infrastructure.storage.record_store import RecordStoreError

INITIAL, RESPONSE, PACKET, OUT_OF_SCOPE = 'INITIAL', 'RESPONSE', 'PACKET', 'OUT_OF_SCOPE'


class ContextAssemblyError(Exception):
    """The Run cannot be inventoried; nothing after this point would be trustworthy."""

    def __init__(self, problems):
        super().__init__('; '.join(problems))
        self.problems = list(problems)


class Candidate(NamedTuple):
    artifact_id: str
    kind: str
    producer_node_id: object   # str or None
    group: str                 # INITIAL | RESPONSE | PACKET | OUT_OF_SCOPE
    content_hash: str
    provenance_ok: bool
    problems: tuple            # ((code, message), ...) - why provenance is not ok, or why out of scope


class UnreadableArtifact(NamedTuple):
    artifact_id: str
    problems: tuple            # ((code, message), ...) from the artifact store


class RunInventory(NamedTuple):
    run_id: str
    run_status: str
    case_id: str
    trial_id: str
    manifest_id: str
    pattern_ref: dict
    route_ref: dict
    protocol_ref: dict
    context: RunContext        # participants is a read-only mapping
    edges: tuple               # ((edgeId, fromNodeId, toNodeId), ...) in route order
    candidates: tuple          # INITIAL in Manifest order, then the rest by artifact id (ASCII)
    unreadable: tuple          # artifacts the store could not verify; their group is unknown


def build_run_context(manifest, route):
    """RunContext for U1 from a frozen Manifest and its Route record. participants is a read-only
    mapping (node id -> frozenset of roles; union over slots)."""
    roles = {}
    for a in manifest['roleAssignments']:
        roles.setdefault(a['nodeId'], set()).add(a['role'])
    participants = MappingProxyType({n: frozenset(r) for n, r in roles.items()})
    system = frozenset(n['nodeId'] for n in manifest['nodes'] if n['kind'] == 'SYSTEM')
    edges = frozenset((e['fromNodeId'], e['toNodeId']) for e in route['edges'])
    return RunContext(participants, system, edges)


def _get(studio, entity, record_id, version=None):
    try:
        return studio.records.get(entity, record_id, version).record()
    except RecordStoreError as e:
        raise ContextAssemblyError([f'{entity} {record_id!r}: {e}']) from None


def _problem_pairs(problems):
    return tuple((p.code, p.message) for p in problems)


def build_inventory(studio, run_id):
    """Read-only inventory of one Run. Raises ContextAssemblyError when the Run, its frozen
    context or any of its initial artifacts cannot be trusted."""
    run = _get(studio, 'run', run_id)
    try:
        status = studio.run_status(run_id)
    except ContextReferenceError as e:
        raise ContextAssemblyError([str(e)]) from None
    _, problems = reconstruct_run(studio, run_id)
    if problems:
        raise ContextAssemblyError([f'run is not reconstructable: {p.code} {p.message}' for p in problems])
    manifest = _get(studio, 'runManifest', run['runManifestId'])
    trial = _get(studio, 'trial', run['trialId'])
    route = _get(studio, 'route', manifest['routeRef']['id'], manifest['routeRef']['version'])
    case_id = manifest['caseId']
    if manifest['runId'] != run_id:  # SR-10; the store checks it only as a whole (verify_store)
        raise ContextAssemblyError([f'manifest {manifest["runManifestId"]} belongs to a different run: {manifest["runId"]}'])
    if trial['caseId'] != case_id or manifest['trialId'] != run['trialId']:
        raise ContextAssemblyError(['run, trial and manifest do not name the same case and trial'])

    initial_ids = list(manifest['initialArtifactIds'])
    dup = sorted({a for a in initial_ids if initial_ids.count(a) > 1})
    if dup:
        raise ContextAssemblyError([f'duplicate id in initialArtifactIds: {d}' for d in dup])

    initial = []
    for aid in initial_ids:
        try:
            art = studio.artifacts.read(aid)
        except StoreError as e:
            raise ContextAssemblyError([f'initial artifact {aid}: {e}']) from None
        meta = art.metadata()
        problems = []
        if not art.event_linked:
            problems.append(f'initial artifact {aid} is ORPHAN (no ARTIFACT_RECEIVED event)')
        if meta['provenance']['caseId'] != case_id:
            problems.append(f'initial artifact {aid} belongs to case {meta["provenance"]["caseId"]}, not {case_id}')
        if problems:
            raise ContextAssemblyError(problems)
        initial.append(Candidate(aid, meta['kind'], meta['producerNodeId'], INITIAL, art.content_hash, True, ()))

    transfers_of_run = {t['transferId'] for t in studio.records.records('transfer') if t['runId'] == run_id}
    others, unreadable = [], []
    for aid in sorted(studio.artifacts.list_ids(), key=lambda a: a.encode('ascii')):
        if aid in initial_ids:
            continue
        try:
            art = studio.artifacts.read(aid)
        except StoreError as e:
            unreadable.append(UnreadableArtifact(aid, _problem_pairs(getattr(e, 'problems', [])) or (('UNREADABLE', str(e)),)))
            continue
        meta = art.metadata()
        prov = meta['provenance']
        claims_run = prov['runId'] == run_id or prov['transferId'] in transfers_of_run
        if prov['caseId'] != case_id and not claims_run:
            continue  # another case's artifact: not part of this run's world
        if not claims_run:
            others.append(Candidate(aid, meta['kind'], meta['producerNodeId'], OUT_OF_SCOPE, art.content_hash, True,
                                    (('NOT_IN_RUN', 'neither an initial artifact of this run nor produced in this run'),)))
            continue
        problems = list(_problem_pairs(trace_artifact(studio, aid).problems))
        if not art.event_linked:
            problems.append(('ORPHAN', 'no ARTIFACT_RECEIVED event records this artifact'))
        if prov['runId'] != run_id:
            problems.append(('PROVENANCE_MISMATCH', f'provenance names run {prov["runId"]!r}, its transfer belongs to {run_id}'))
        if meta['kind'] == 'RAW_RESPONSE':
            group = RESPONSE
        elif meta['kind'] == 'PACKET':
            group = PACKET
        else:
            group = OUT_OF_SCOPE
            problems.append(('KIND_NOT_COLLECTED', f'{meta["kind"]} artifacts are not response candidates'))
        provenance_ok = not [p for p in problems if p[0] != 'KIND_NOT_COLLECTED']
        others.append(Candidate(aid, meta['kind'], meta['producerNodeId'], group, art.content_hash, provenance_ok,
                                tuple(problems)))

    return RunInventory(
        run_id=run_id, run_status=status, case_id=case_id, trial_id=run['trialId'],
        manifest_id=manifest['runManifestId'], pattern_ref=dict(manifest['patternRef']),
        route_ref=dict(manifest['routeRef']), protocol_ref=dict(manifest['protocolRef']),
        context=build_run_context(manifest, route),
        edges=tuple((e['edgeId'], e['fromNodeId'], e['toNodeId']) for e in route['edges']),
        candidates=tuple(initial + others), unreadable=tuple(unreadable))
