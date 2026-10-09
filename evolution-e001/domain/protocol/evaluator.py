"""Deterministic RECEIVE evaluation of an E001 Protocol (E001-F1/U1).

A pure function: given a Protocol record and the frozen context of a Run, decide whether one
artifact may be placed in the packet sent to one node along one edge. No I/O, no clock, no
randomness. Packet assembly, enforcement in record_transfer and event recording are later units.

Only RECEIVE rules are evaluated. SUBMIT rules are validated as part of the Protocol but never
evaluated here; every Decision lists them in `unevaluated_rule_ids` so that nothing reads as if
SUBMIT were enforced.

Three outcomes are kept apart:
  * a Decision with effect DENY  - a valid Protocol said no (RULE_DENY or DEFAULT_DENY)
  * ProtocolInvalid              - the Protocol itself cannot be trusted; nothing is evaluated
  * EvaluationInputInvalid       - the caller passed a context that does not hold together

Semantics: docs/TRACEABILITY.md §3.
"""
import re
from typing import NamedTuple

from domain.schema import validator

_DEFS = validator.load_schema()['$defs']
ROLES = frozenset(_DEFS['role']['enum'])
ARTIFACT_KINDS = frozenset(_DEFS['artifactKind']['enum'])
_NODE_ID = re.compile(_DEFS['nodeId']['pattern'])

NONE, SELF, ROUTE_PREDECESSOR, PEER, UNRELATED = 'NONE', 'SELF', 'ROUTE_PREDECESSOR', 'PEER', 'UNRELATED'


class ProtocolInvalid(Exception):
    def __init__(self, problems):
        super().__init__('; '.join(problems))
        self.problems = list(problems)


class EvaluationInputInvalid(Exception):
    def __init__(self, problems):
        super().__init__('; '.join(problems))
        self.problems = list(problems)


class ArtifactRef(NamedTuple):
    artifact_id: str
    kind: str
    producer_node_id: object  # str or None (case-level artifact)


class RunContext(NamedTuple):
    """Frozen facts of one Run: who takes part in which roles, which nodes are SYSTEM, which edges exist."""
    participants: dict        # nodeId -> frozenset of roles (from runManifest.roleAssignments)
    system_node_ids: frozenset
    route_edges: frozenset    # {(fromNodeId, toNodeId)} of the run's frozen route


class Decision(NamedTuple):
    artifact_id: str
    effect: str               # ALLOW | DENY
    basis: str                # RULE_ALLOW | RULE_DENY | DEFAULT_DENY
    relation: str             # NONE | SELF | ROUTE_PREDECESSOR | PEER | UNRELATED
    allow_rule_ids: tuple
    deny_rule_ids: tuple
    evaluated_action: str     # always RECEIVE
    unevaluated_rule_ids: tuple


def check_protocol(protocol):
    """Schema validation plus the meaning checks the schema cannot express. Raises ProtocolInvalid."""
    issues = validator.validate_record('protocol', protocol)
    if issues:
        raise ProtocolInvalid([f'schema: {i.path}: {i.message}' for i in issues])
    problems, seen = [], set()
    for rule in protocol['rules']:
        rid, subj = rule['ruleId'], rule['subject']
        if rid in seen:
            problems.append(f'duplicate ruleId {rid!r}')
        seen.add(rid)
        kind, value = subj['type'], subj.get('value')
        if kind == 'ANY' and 'value' in subj:
            problems.append(f'{rid}: subject ANY must not carry a value')
        elif kind == 'ROLE' and value not in ROLES:
            problems.append(f'{rid}: subject ROLE needs a known role, got {value!r}')
        elif kind == 'NODE' and (not isinstance(value, str) or not _NODE_ID.match(value)):
            problems.append(f'{rid}: subject NODE needs a node id, got {value!r}')
    if problems:
        raise ProtocolInvalid(problems)


def _check_input(context, subject_node_id, dispatch_edge, artifact):
    problems = []
    for node, roles in context.participants.items():
        if not set(roles) <= ROLES:
            problems.append(f'participant {node} has unknown roles {sorted(set(roles) - ROLES)}')
    if subject_node_id not in context.participants or subject_node_id in context.system_node_ids:
        problems.append(f'subject {subject_node_id!r} is not a participant of the run')
    if not (isinstance(dispatch_edge, tuple) and len(dispatch_edge) == 2):
        problems.append(f'dispatch edge must be a (from, to) pair, got {dispatch_edge!r}')
    else:
        if dispatch_edge[1] != subject_node_id:
            problems.append(f'dispatch edge {dispatch_edge} does not end at the subject')
        if dispatch_edge not in context.route_edges:
            problems.append(f'dispatch edge {dispatch_edge} is not in the run\'s route')
    if artifact.kind not in ARTIFACT_KINDS:
        problems.append(f'artifact {artifact.artifact_id}: unknown kind {artifact.kind!r}')
    p = artifact.producer_node_id
    if p is not None and (not isinstance(p, str) or not _NODE_ID.match(p)):
        problems.append(f'artifact {artifact.artifact_id}: malformed producer {p!r}')
    if problems:
        raise EvaluationInputInvalid(problems)


def relation_of(context, subject_node_id, producer_node_id):
    """The single relation of an artifact's producer to the subject (first match wins)."""
    if producer_node_id is None:
        return NONE
    if producer_node_id in context.system_node_ids:
        return UNRELATED
    if producer_node_id == subject_node_id:
        return SELF
    if producer_node_id not in context.participants:
        return UNRELATED
    if (producer_node_id, subject_node_id) in context.route_edges:
        return ROUTE_PREDECESSOR
    return PEER


def _subject_matches(subject, node_id, roles):
    kind = subject['type']
    if kind == 'ANY':
        return True
    if kind == 'ROLE':
        return subject['value'] in roles
    return subject['value'] == node_id  # NODE; a node outside the run simply never matches


def _relation_matches(wanted, relation):
    if relation == UNRELATED:
        return False
    return wanted == 'ANY' or wanted == relation


def _condition_holds(condition, context, dispatch_edge, producer_node_id):
    if condition == 'ALWAYS':
        return True
    # ROUTE_EDGE_EXISTS: the artifact travels on an edge that starts at its origin
    # (a case-level artifact's origin is a SYSTEM node).
    origin = dispatch_edge[0]
    if producer_node_id is None:
        return origin in context.system_node_ids
    return origin == producer_node_id


def evaluate_receive(protocol, context, subject_node_id, dispatch_edge, artifact):
    """Decide whether `artifact` may be placed in the packet for `subject_node_id` on `dispatch_edge`."""
    check_protocol(protocol)
    _check_input(context, subject_node_id, dispatch_edge, artifact)
    roles = frozenset(context.participants[subject_node_id])
    relation = relation_of(context, subject_node_id, artifact.producer_node_id)
    allow, deny, unevaluated = [], [], []
    for rule in protocol['rules']:
        if rule['action'] != 'RECEIVE':
            unevaluated.append(rule['ruleId'])
            continue
        obj = rule['object']
        if (_subject_matches(rule['subject'], subject_node_id, roles)
                and artifact.kind in obj['artifactKinds']
                and _relation_matches(obj['producerRelation'], relation)
                and _condition_holds(rule['condition'], context, dispatch_edge, artifact.producer_node_id)):
            (deny if rule['effect'] == 'DENY' else allow).append(rule['ruleId'])
    if deny:
        effect, basis = 'DENY', 'RULE_DENY'
    elif allow:
        effect, basis = 'ALLOW', 'RULE_ALLOW'
    else:
        effect, basis = 'DENY', 'DEFAULT_DENY'
    return Decision(artifact.artifact_id, effect, basis, relation, tuple(allow), tuple(deny), 'RECEIVE', tuple(unevaluated))
