"""E001-F1/U3-2: candidate selection for one edge of one Run (read only).

Spec: reports/U3-2_MINIMAL_IMPLEMENTATION_SPEC_002.md (review copy). For the edge's destination,
every candidate of the U3-1 inventory is decided in order, and the first stage it fails decides it:

  S1  group is INITIAL or RESPONSE                                  (D1)
  S2  a RESPONSE is tied to a Transfer of this Run                  (U-1)
  S3  its kind is in the edge's allowedArtifactKinds               (U-5)
  S4  U1 evaluate_receive says ALLOW                               (Protocol)
  S5  no other ALLOWed RESPONSE of the same producer in this call  (D2 / U-2)

U1 sees only candidates that passed S1-S3, so an ALLOW never stands in for run membership, transfer
provenance or the edge's kinds. A DENY is an ordinary result, not a violation (D4). When the inventory
cannot be built, the edge is not in the route, the Protocol or the U1 input is invalid, or the store
changed under the inventory, the whole call is INCOMPLETE and decides nothing (D3 / U-3).

Nothing is written; no packet is built (U3-3); BLOCK / PAUSE is not handled here (U-4). The status
names below are return values of this module only, not Schema states.
"""
from typing import NamedTuple

from application.context_assembly import INITIAL, RESPONSE, ContextAssemblyError, build_inventory
from domain.protocol import evaluator
from domain.protocol.evaluator import ArtifactRef, EvaluationInputInvalid, ProtocolInvalid
from infrastructure.storage.artifact_store import StoreError
from infrastructure.storage.record_store import RecordStoreError

EVALUATED, INCOMPLETE = 'EVALUATED', 'INCOMPLETE'
SELECTABLE_GROUPS = (INITIAL, RESPONSE)


class Rejection(NamedTuple):
    artifact_id: str
    stage: str                # S1 | S2 | S3 | S4
    code: str
    detail: tuple             # ((code, message), ...) carried over, or U1 basis and rule ids


class Unresolved(NamedTuple):
    artifact_id: str
    code: str                 # MULTIPLE_RESPONSES_SAME_PRODUCER
    producer_node_id: str


class Selection(NamedTuple):
    status: str               # EVALUATED | INCOMPLETE
    run_id: str
    edge_id: str
    from_node_id: object      # None when INCOMPLETE before the edge was found
    to_node_id: object
    protocol_ref: object
    allowed_kinds: tuple
    accepted: tuple           # artifact ids, inventory order (Manifest order, then ASCII)
    rejected: tuple           # Rejection, inventory order
    unresolved: tuple         # Unresolved, inventory order
    diagnostics: tuple        # (('UNREADABLE', artifact_id, problems), ...) - scope unknown, not a stop
    unevaluated_rule_ids: tuple   # SUBMIT rules: never evaluated (G-A5), listed whether or not U1 was called
    problems: tuple           # ((code, message), ...) why the call is INCOMPLETE; empty otherwise


def _incomplete(run_id, edge_id, problems, edge=None, protocol_ref=None):
    return Selection(INCOMPLETE, run_id, edge_id, edge and edge['fromNodeId'], edge and edge['toNodeId'], protocol_ref,
                     tuple(edge['allowedArtifactKinds']) if edge else (), (), (), (), (), (), tuple(problems))


def _transfer_problem(meta, run_id, cand, transfers):
    """S2 for one RESPONSE: the code of the first condition it fails, or None."""
    prov = meta['provenance']
    if prov['transferId'] is None:
        return 'RESPONSE_WITHOUT_TRANSFER'
    t = transfers.get(prov['transferId'])
    if t is None or t['runId'] != run_id:
        return 'TRANSFER_NOT_IN_RUN'
    if prov['runId'] != run_id:
        return 'PROVENANCE_MISMATCH'
    if not cand.provenance_ok:
        return 'PROVENANCE_NOT_OK'
    return None


def select_candidates(studio, run_id, edge_id):
    """Decide every inventory candidate for the packet sent on `edge_id` of `run_id`. Read only."""
    try:
        inv = build_inventory(studio, run_id)
    except ContextAssemblyError as e:
        return _incomplete(run_id, edge_id, [('INVENTORY_FAILED', p) for p in e.problems])
    try:
        manifest = studio.manifest_of(run_id)
        route = studio.records.get('route', inv.route_ref['id'], inv.route_ref['version']).record()
        protocol = studio.records.get('protocol', inv.protocol_ref['id'], inv.protocol_ref['version']).record()
        transfers = {t['transferId']: t for t in studio.records.records('transfer')}
    except RecordStoreError as e:
        return _incomplete(run_id, edge_id, [('RECORD_UNREADABLE', str(e))])
    edge = next((e for e in route['edges'] if e['edgeId'] == edge_id), None)
    if edge is None:
        return _incomplete(run_id, edge_id, [('EDGE_NOT_IN_ROUTE', f'edge {edge_id!r} is not in the run\'s route')],
                           protocol_ref=inv.protocol_ref)
    if manifest['runManifestId'] != inv.manifest_id:
        return _incomplete(run_id, edge_id, [('INVENTORY_CHANGED', 'the run now names another manifest')], edge, inv.protocol_ref)
    allowed_kinds = frozenset(edge['allowedArtifactKinds'])
    dispatch = (edge['fromNodeId'], edge['toNodeId'])

    rejected, allowed = [], []
    for cand in inv.candidates:
        aid = cand.artifact_id
        if cand.group not in SELECTABLE_GROUPS:                                           # S1
            rejected.append(Rejection(aid, 'S1', 'GROUP_NOT_SELECTABLE', ((cand.group, ''),) + cand.problems))
            continue
        if cand.group == RESPONSE:                                                        # S2
            try:
                meta = studio.artifacts.read(aid).metadata()
            except StoreError as e:
                return _incomplete(run_id, edge_id, [('INVENTORY_CHANGED', f'{aid}: {e}')], edge, inv.protocol_ref)
            if meta['contentHash'] != cand.content_hash:
                return _incomplete(run_id, edge_id, [('INVENTORY_CHANGED', f'{aid}: content hash differs from the inventory')],
                                   edge, inv.protocol_ref)
            code = _transfer_problem(meta, run_id, cand, transfers)
            if code is not None:
                rejected.append(Rejection(aid, 'S2', code, cand.problems))
                continue
        if cand.kind not in allowed_kinds:                                                # S3
            rejected.append(Rejection(aid, 'S3', 'KIND_NOT_ALLOWED_ON_EDGE', ((cand.kind, ''),)))
            continue
        try:                                                                              # S4
            decision = evaluator.evaluate_receive(protocol, inv.context, edge['toNodeId'], dispatch,
                                                  ArtifactRef(aid, cand.kind, cand.producer_node_id))
        except ProtocolInvalid as e:
            return _incomplete(run_id, edge_id, [('PROTOCOL_INVALID', p) for p in e.problems], edge, inv.protocol_ref)
        except EvaluationInputInvalid as e:
            return _incomplete(run_id, edge_id, [('EVALUATION_INPUT_INVALID', p) for p in e.problems], edge, inv.protocol_ref)
        if decision.effect != 'ALLOW':
            rejected.append(Rejection(aid, 'S4', 'PROTOCOL_DENY', ((decision.basis, ''),) + tuple((r, '') for r in decision.deny_rule_ids)))
            continue
        allowed.append(cand)

    # S5: count only what passed S1-S4, RESPONSEs only, within this call
    per_producer = {}
    for cand in allowed:
        if cand.group == RESPONSE:
            per_producer[cand.producer_node_id] = per_producer.get(cand.producer_node_id, 0) + 1
    accepted, unresolved = [], []
    for cand in allowed:
        if cand.group == RESPONSE and per_producer[cand.producer_node_id] > 1:
            unresolved.append(Unresolved(cand.artifact_id, 'MULTIPLE_RESPONSES_SAME_PRODUCER', cand.producer_node_id))
        else:
            accepted.append(cand.artifact_id)

    return Selection(EVALUATED, run_id, edge_id, edge['fromNodeId'], edge['toNodeId'], inv.protocol_ref,
                     tuple(edge['allowedArtifactKinds']), tuple(accepted), tuple(rejected), tuple(unresolved),
                     tuple(('UNREADABLE', u.artifact_id, u.problems) for u in inv.unreadable),
                     tuple(r['ruleId'] for r in protocol['rules'] if r['action'] != 'RECEIVE'), ())
