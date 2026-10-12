"""E001-F1/U4 (minimal, plan A): store the packet of one edge and record its Transfer.

Spec: reports/U4_MINIMAL_IMPLEMENTATION_SPEC_002.md (review copy). Only existing APIs are used:
select_candidates (U3-2), assemble_packet (U3-3), ArtifactStore.create, Studio.record_transfer,
trace_artifact and verify_store. Nothing existing is changed, deleted or overwritten.

The writes are NOT atomic. Packet artifact, ARTIFACT_RECEIVED, transfer record and TRANSFER_CREATED are
separate files; a failure between them leaves what was already written. Every call therefore starts
with a read-only diagnosis of the run and edge, and reports a half-written state as PARTIAL_STATE
instead of hiding it behind new ids. Only a packet that is complete, event-linked, identical to the
packet assembled now and not yet carried by a Transfer may be resumed - and only when the caller
passes its packet id and transfer id explicitly.

At most one Transfer per run and edge (first version). Concurrent writers are not supported. The status
names and reason codes are return values of this module only, not Schema states.
"""
from typing import NamedTuple

from application.candidate_selection import select_candidates
from application.execution_context import ContextReferenceError, ExecutionContextError, new_id
from application.packet_assembly import ASSEMBLED, assemble_packet
from application.provenance import trace_artifact, verify_store
from domain.protocol.packet import PacketFormatError, decode_packet, packet_sha256
from infrastructure.storage.artifact_store import ArtifactIntegrityError, StoreError
from infrastructure.storage.event_log import SYSTEM_ACTOR_ID, EventSequenceCollision, InvalidEvent
from infrastructure.storage.record_store import RecordStoreError

RECORDED, ALREADY_RECORDED, NOT_RECORDED, PARTIAL_STATE = 'RECORDED', 'ALREADY_RECORDED', 'NOT_RECORDED', 'PARTIAL_STATE'
# what a failing write in the existing store APIs can raise (anything else propagates)
_WRITE_ERRORS = (StoreError, RecordStoreError, OSError, EventSequenceCollision, InvalidEvent)


class PersistResult(NamedTuple):
    status: str
    reasons: tuple               # ((code, message), ...)
    run_id: str
    edge_id: str
    packet_artifact_id: object   # str or None
    transfer_id: object          # str or None
    packet_sha256: object        # str or None
    wrote: bool                  # this call wrote (or may have written) something
    resumable: bool              # PARTIAL_STATE only: an explicit resume request can finish it
    retry_allowed: bool          # calling again with the same arguments is safe
    needs_human: bool
    store_check_complete: bool   # verify_store ran all of its checks
    store_problems: tuple        # store problems not attributed to this call (relation unchecked)


def _result(status, reasons, run_id, edge_id, *, packet_id=None, transfer_id=None, sha=None, wrote=False,
            resumable=False, retry_allowed=False, needs_human=False, store_check_complete=False, store_problems=()):
    return PersistResult(status, tuple(reasons), run_id, edge_id, packet_id, transfer_id, sha, wrote, resumable,
                         retry_allowed, needs_human, store_check_complete, tuple(store_problems))


class _Diagnosis(NamedTuple):
    incomplete: tuple    # artifact ids without artifact.json (run and edge unknown)
    unknown: tuple       # PACKETs of this run whose edge cannot be read
    orphans: tuple       # PACKETs of this run and edge without ARTIFACT_RECEIVED
    pending: tuple       # (artifact id, transfer id, content): complete PACKETs of this run and edge, no Transfer yet
    strays: tuple        # PACKETs of this run and edge naming a Transfer that carries another packet
    transfers: tuple     # transfer records of this run and edge


def _diagnose(studio, run_id, edge_id):
    """Read-only scan of what earlier calls left for this run and edge."""
    all_transfers = {t['transferId']: t for t in studio.records.records('transfer')}
    incomplete, unknown, orphans, pending, strays = [], [], [], [], []
    for aid in studio.artifacts.list_ids():
        try:
            art = studio.artifacts.read(aid)
        except ArtifactIntegrityError as e:
            if any(p.code == 'INCOMPLETE' for p in e.problems):
                incomplete.append(aid)
            continue  # a corrupt artifact is not a packet left by U4; U3-1 lists it as unreadable
        meta = art.metadata()
        prov = meta['provenance']
        if meta['kind'] != 'PACKET' or prov['runId'] != run_id:
            continue
        owner = all_transfers.get(prov['transferId'])
        if art.event_linked and owner is not None and owner['packetArtifactId'] == aid:
            continue  # carried by its own transfer: that transfer is checked below if it is on this edge
        try:
            header = decode_packet(art.content).header
        except PacketFormatError:
            unknown.append(aid)
            continue
        if header.edge_id != edge_id:
            continue
        if not art.event_linked:
            orphans.append(aid)
            continue
        if owner is None:
            pending.append((aid, prov['transferId'], art.content))
        elif owner['packetArtifactId'] != aid:
            strays.append(aid)
    transfers = [t for t in all_transfers.values() if t['runId'] == run_id and t['edgeId'] == edge_id]
    return _Diagnosis(tuple(incomplete), tuple(unknown), tuple(orphans), tuple(pending), tuple(strays), tuple(transfers))


def _check_recorded(studio, transfer, run_id, edge_id):
    """Problems that keep a recorded transfer from counting as complete; [] means complete."""
    tid, pid = transfer['transferId'], transfer['packetArtifactId']
    problems = []
    if (transfer['runId'], transfer['edgeId']) != (run_id, edge_id):
        problems.append(('TRANSFER_MISMATCH', f'{tid} is for {transfer["runId"]}/{transfer["edgeId"]}'))
    created = [e for e in studio.events.read_all()[0]
               if e['eventType'] == 'TRANSFER_CREATED' and e['targetType'] == 'transfer' and e['targetId'] == tid]
    if not created:
        problems.append(('TRANSFER_UNANCHORED', f'{tid} has no TRANSFER_CREATED event'))
    elif len(created) > 1:
        problems.append(('TRANSFER_EVENT_DUPLICATED', f'{tid} has {len(created)} TRANSFER_CREATED events'))
    elif created[0]['payload'].get('packetHash') != transfer['packetHash']:
        problems.append(('TRANSFER_EVENT_MISMATCH', f'TRANSFER_CREATED of {tid} names another packet hash'))
    try:
        art = studio.artifacts.read(pid)
    except (StoreError, OSError) as e:
        return problems + [('PACKET_UNREADABLE', f'{pid}: {e}')]
    meta = art.metadata()
    prov = meta['provenance']
    if not art.event_linked:
        problems.append(('PACKET_ORPHAN', f'{pid} has no ARTIFACT_RECEIVED event'))
    if (meta['kind'], prov['runId'], prov['transferId'], meta['producerNodeId']) != ('PACKET', run_id, tid, SYSTEM_ACTOR_ID):
        problems.append(('PACKET_MISMATCH', f'{pid} is not the packet of {tid}'))
    if art.content_hash != transfer['packetHash']:
        problems.append(('PACKET_HASH_MISMATCH', f'{pid} content differs from the packetHash of {tid}'))
    try:
        packet = decode_packet(art.content)
    except PacketFormatError as e:
        return problems + [('PACKET_UNDECODABLE', f'{pid}: {e}')]
    if (packet.header.run_id, packet.header.edge_id) != (run_id, edge_id):
        problems.append(('PACKET_HEADER_MISMATCH', f'{pid} header names {packet.header.run_id}/{packet.header.edge_id}'))
    if [a.artifact_id for a in packet.artifacts] != list(transfer['artifactIds']):
        problems.append(('ARTIFACT_IDS_MISMATCH', f'{pid} carries other ids than {tid}'))
    problems += [(p.code, p.message) for p in trace_artifact(studio, pid).problems]
    return problems


def _precheck(studio, selection, assembled):
    """Packet content against the selection and the store as it is now; [] means consistent."""
    problems = []
    try:
        packet = decode_packet(assembled.packet_bytes)
    except PacketFormatError as e:
        return [('PRECHECK_MISMATCH', f'packet does not decode: {e}')]
    h = packet.header
    want = (selection.run_id, selection.edge_id, selection.from_node_id, selection.to_node_id,
            selection.protocol_ref['id'], selection.protocol_ref['version'])
    if (h.run_id, h.edge_id, h.from_node_id, h.to_node_id, h.protocol_id, h.protocol_version) != want:
        problems.append(('PRECHECK_MISMATCH', 'packet header differs from the selection'))
    ids = [a.artifact_id for a in packet.artifacts]
    if ids != list(assembled.artifact_ids):
        problems.append(('PRECHECK_MISMATCH', 'artifact order in the packet differs from assembled.artifact_ids'))
    if sorted(ids) != sorted(selection.accepted):
        problems.append(('PRECHECK_MISMATCH', 'artifact ids in the packet differ from selection.accepted'))
    for a in packet.artifacts:
        try:
            stored = studio.artifacts.read(a.artifact_id)
        except (StoreError, OSError) as e:
            problems.append(('PRECHECK_MISMATCH', f'{a.artifact_id} cannot be read now: {e}'))
            continue
        if stored.content != a.content:
            problems.append(('PRECHECK_MISMATCH', f'{a.artifact_id} in the store differs from its packet content'))
    if packet_sha256(assembled.packet_bytes) != assembled.packet_sha256:
        problems.append(('PRECHECK_MISMATCH', 'packet_sha256 does not match the packet bytes'))
    return problems


def _record_and_check(studio, run_id, edge_id, packet_id, transfer_id, assembled):
    """record_transfer for a stored packet, then the post-write checks. Returns a PersistResult."""
    sha = assembled.packet_sha256
    ids = dict(packet_id=packet_id, transfer_id=transfer_id, sha=sha, wrote=True)
    try:
        studio.record_transfer(run_id, edge_id, packet_id, artifact_ids=assembled.artifact_ids, transfer_id=transfer_id,
                               isolation_assurance='PACKET_CONTROLLED', status='PACKET_ASSEMBLED')
    except ExecutionContextError as e:
        return _result(PARTIAL_STATE, [('TRANSFER_REJECTED', str(e))], run_id, edge_id, resumable=True, needs_human=True, **ids)
    except _WRITE_ERRORS as e:
        if studio.records.exists('transfer', transfer_id):
            return _result(PARTIAL_STATE, [('TRANSFER_UNANCHORED', f'{type(e).__name__}: {e}')], run_id, edge_id,
                           needs_human=True, **ids)
        return _result(PARTIAL_STATE, [('TRANSFER_NOT_CREATED', f'{type(e).__name__}: {e}')], run_id, edge_id,
                       resumable=True, needs_human=False, **ids)
    transfer = studio.records.get('transfer', transfer_id).record()
    problems = _check_recorded(studio, transfer, run_id, edge_id)
    if transfer['packetHash'] != sha:
        problems.append(('PACKET_HASH_MISMATCH', 'recorded packetHash differs from the assembled packet'))
    if problems:
        return _result(PARTIAL_STATE, [('POSTCHECK_FAILED', f'{c}: {m}') for c, m in problems], run_id, edge_id,
                       needs_human=True, **ids)
    base = list(studio.artifacts.verify()) + list(studio.records.verify())
    found = verify_store(studio)
    mine = (run_id, packet_id, transfer_id)
    related = [p for p in found if any(x in f'{p.subject} {p.message}' for x in mine)]
    other = [(p.code, p.subject, p.message) for p in found if p not in related]
    if related:
        return _result(PARTIAL_STATE, [('POSTCHECK_FAILED', f'{p.code} {p.subject}: {p.message}') for p in related],
                       run_id, edge_id, needs_human=True, store_check_complete=not base, store_problems=other, **ids)
    return _result(RECORDED, (), run_id, edge_id, retry_allowed=True, store_check_complete=not base,
                   store_problems=other, **ids)


def persist_packet(studio, run_id, edge_id, transfer_id=None, packet_artifact_id=None):
    """Store the packet for `edge_id` of `run_id` and record its Transfer (plan A). See the module docstring."""
    resume_requested = transfer_id is not None or packet_artifact_id is not None
    try:
        d = _diagnose(studio, run_id, edge_id)
    except OSError as e:
        return _result(NOT_RECORDED, [('DIAGNOSIS_UNREADABLE', f'{type(e).__name__}: {e}')], run_id, edge_id, needs_human=True)
    partial = []
    if d.incomplete:
        partial.append(('PACKET_INCOMPLETE', f'artifacts without artifact.json (run unknown): {list(d.incomplete)}'))
    if d.unknown:
        partial.append(('PACKET_UNDECODABLE', f'packets of this run whose edge cannot be read: {list(d.unknown)}'))
    if d.orphans:
        partial.append(('PACKET_ORPHAN', f'packets of this edge without ARTIFACT_RECEIVED: {list(d.orphans)}'))
    if d.strays:
        partial.append(('STRAY_PACKET', f'packets of this edge not carried by the transfer they name: {list(d.strays)}'))
    if len(d.transfers) > 1:
        partial.append(('MULTIPLE_TRANSFERS', f'{len(d.transfers)} transfers on this edge'))
    if d.transfers and d.pending:
        partial.append(('PACKET_WITHOUT_TRANSFER', f'extra packets next to an existing transfer: {[p[0] for p in d.pending]}'))
    if len(d.pending) > 1:
        partial.append(('MULTIPLE_PENDING_PACKETS', f'{[p[0] for p in d.pending]}'))
    if partial:
        return _result(PARTIAL_STATE, partial, run_id, edge_id, needs_human=True)

    selection = select_candidates(studio, run_id, edge_id)
    assembled = assemble_packet(studio, selection)
    built = assembled.status == ASSEMBLED

    if d.transfers:                                                           # one transfer on this edge
        t = d.transfers[0]
        tid, pid = t['transferId'], t['packetArtifactId']
        problems = _check_recorded(studio, t, run_id, edge_id)
        if problems:
            code = 'TRANSFER_UNANCHORED' if any(c == 'TRANSFER_UNANCHORED' for c, _ in problems) else 'POSTCHECK_FAILED'
            return _result(PARTIAL_STATE, [(code, f'{c}: {m}') for c, m in problems], run_id, edge_id, packet_id=pid,
                           transfer_id=tid, sha=t['packetHash'], needs_human=True)
        if built and t['packetHash'] == assembled.packet_sha256:
            return _result(ALREADY_RECORDED, (), run_id, edge_id, packet_id=pid, transfer_id=tid, sha=t['packetHash'],
                           retry_allowed=True)
        why = [('EDGE_ALREADY_HAS_TRANSFER', f'{tid} carries another packet than the one assembled now')]
        return _result(NOT_RECORDED, why + list(assembled.problems), run_id, edge_id, packet_id=pid, transfer_id=tid,
                       sha=t['packetHash'], needs_human=True)

    if d.pending:                                                             # a stored packet without its transfer
        pid, tid, content = d.pending[0]
        resumable = built and content == assembled.packet_bytes
        if resume_requested and (packet_artifact_id, transfer_id) == (pid, tid) and resumable:
            problems = _precheck(studio, selection, assembled)
            if problems:
                return _result(PARTIAL_STATE, problems, run_id, edge_id, packet_id=pid, transfer_id=tid,
                               sha=assembled.packet_sha256, needs_human=True)
            return _record_and_check(studio, run_id, edge_id, pid, tid, assembled)
        reason = [('PACKET_WITHOUT_TRANSFER', f'{pid} names {tid}, which does not exist'
                   + ('' if resumable else '; it differs from the packet assembled now, so it cannot be resumed'))]
        return _result(PARTIAL_STATE, reason, run_id, edge_id, packet_id=pid, transfer_id=tid,
                       sha=assembled.packet_sha256 if resumable else None, resumable=resumable, needs_human=not resumable)

    if resume_requested:
        return _result(NOT_RECORDED, [('RESUME_TARGET_NOT_FOUND', f'no stored packet {packet_artifact_id!r} for {transfer_id!r}')],
                       run_id, edge_id, needs_human=True)
    if not built:
        return _result(NOT_RECORDED, assembled.problems, run_id, edge_id, retry_allowed=True)
    problems = _precheck(studio, selection, assembled)
    if problems:
        return _result(NOT_RECORDED, problems, run_id, edge_id, needs_human=True)
    try:
        case_id = studio.manifest_of(run_id)['caseId']
    except (ContextReferenceError, RecordStoreError, OSError) as e:
        return _result(NOT_RECORDED, [('RECORD_UNREADABLE', f'manifest of {run_id}: {e}')], run_id, edge_id, needs_human=True)

    tid, pid = new_id('XFER'), new_id('ART')
    try:
        stored = studio.artifacts.create(content=assembled.packet_bytes, kind='PACKET', case_id=case_id, source_kind='SYSTEM',
                                         received_via='SYSTEM_GENERATED', run_id=run_id, transfer_id=tid,
                                         producer_node_id=SYSTEM_ACTOR_ID, parent_artifact_ids=[], artifact_id=pid)
    except _WRITE_ERRORS as e:
        if not (studio.artifacts.artifacts_dir / pid).exists():
            return _result(NOT_RECORDED, [('PACKET_NOT_STORED', f'{type(e).__name__}: {e}')], run_id, edge_id, retry_allowed=True)
        state = studio.artifacts.inspect(pid).status
        code = {'INCOMPLETE': 'PACKET_INCOMPLETE', 'ORPHAN': 'PACKET_ORPHAN'}.get(state, 'PACKET_CORRUPT')
        return _result(PARTIAL_STATE, [(code, f'{type(e).__name__}: {e}')], run_id, edge_id, packet_id=pid, transfer_id=tid,
                       sha=assembled.packet_sha256, wrote=True, needs_human=True)
    if stored.content_hash != assembled.packet_sha256:
        return _result(PARTIAL_STATE, [('POSTCHECK_FAILED', 'stored packet hash differs from the assembled packet')], run_id,
                       edge_id, packet_id=pid, transfer_id=tid, sha=assembled.packet_sha256, wrote=True, needs_human=True)
    return _record_and_check(studio, run_id, edge_id, pid, tid, assembled)
