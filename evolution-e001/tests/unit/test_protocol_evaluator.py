"""E001-F1/U1: deterministic RECEIVE evaluation (domain/protocol/evaluator.py).

Contexts are built from the committed E001-B fixture (protocols, routes, patterns, nodes), so
the P001/P002 cases use the same records as the rest of the suite. Semantics: docs/TRACEABILITY.md §3.
"""
import copy
import itertools
import json
import unittest
from pathlib import Path

from domain.protocol.evaluator import (ArtifactRef, Decision, EvaluationInputInvalid, ProtocolInvalid, RunContext,
                                       evaluate_receive)

FIXTURE = json.loads((Path(__file__).resolve().parents[1] / 'fixtures' / 'p001_bundle_valid.json').read_text(encoding='utf-8'))
REC = FIXTURE['records']
PROTO = {p['protocolId']: p for p in REC['protocol']}
STUDIO, A, B, C, H = 'NODE-studio', 'NODE-observer-a', 'NODE-observer-b', 'NODE-observer-c', 'NODE-human-01'


def context(route_id, extra_roles=None):
    route = next(r for r in REC['route'] if r['routeId'] == route_id)
    pattern = next(p for p in REC['pattern'] if p['patternId'] == route['patternRef']['id'])
    slot_roles = {s['slotId']: s['role'] for s in pattern['slots']}
    participants = {}
    for b in route['slotBindings']:
        participants.setdefault(b['nodeId'], set()).add(slot_roles[b['slotId']])
    for node, role in (extra_roles or {}).items():
        participants.setdefault(node, set()).add(role)
    return RunContext({n: frozenset(r) for n, r in participants.items()},
                      frozenset(n['nodeId'] for n in REC['node'] if n['kind'] == 'SYSTEM'),
                      frozenset((e['fromNodeId'], e['toNodeId']) for e in route['edges']))


P001, P002 = context('ROUTE-P001-FIX'), context('ROUTE-P002-FIX')
CASE_IN = ArtifactRef('ART-case-input', 'CASE_INPUT', None)


def raw(producer):
    return ArtifactRef(f'ART-raw-{producer[-1]}', 'RAW_RESPONSE', producer)


def protocol(*rules):
    return {'schemaVersion': 'E001/1.0', 'protocolId': 'PROTO-test', 'version': '1.0', 'defaultEffect': 'DENY',
            'conflictResolution': 'DENY_OVERRIDES', 'violationAction': 'BLOCK', 'rules': list(rules)}


def rule(rid, effect='ALLOW', subject=None, action='RECEIVE', kinds=('RAW_RESPONSE',), relation='ANY', condition='ALWAYS'):
    return {'ruleId': rid, 'subject': subject or {'type': 'ANY'}, 'action': action,
            'object': {'artifactKinds': list(kinds), 'producerRelation': relation}, 'effect': effect, 'condition': condition}


def ev(proto, ctx, subject, edge, artifact):
    return evaluate_receive(proto, ctx, subject, edge, artifact)


class RelationTests(unittest.TestCase):
    def relation(self, ctx, subject, edge, producer):
        return ev(protocol(), ctx, subject, edge, ArtifactRef('ART-x-001', 'RAW_RESPONSE', producer)).relation

    def test_each_relation(self):
        self.assertEqual(self.relation(P001, A, (STUDIO, A), None), 'NONE')
        self.assertEqual(self.relation(P001, A, (STUDIO, A), A), 'SELF')
        self.assertEqual(self.relation(P002, B, (A, B), A), 'ROUTE_PREDECESSOR')
        self.assertEqual(self.relation(P001, A, (STUDIO, A), B), 'PEER')
        self.assertEqual(self.relation(P001, A, (STUDIO, A), STUDIO), 'UNRELATED')            # SYSTEM author (G-A2)
        self.assertEqual(self.relation(P001, A, (STUDIO, A), 'NODE-other-run'), 'UNRELATED')  # not a participant (E-1)

    def test_system_author_is_unrelated_even_when_it_fills_a_role(self):
        # A SYSTEM node bound to a slot is still a SYSTEM author (G-A2); without that rule it would be a PEER.
        tool = 'NODE-sys-tool'
        ctx = RunContext({**P001.participants, tool: frozenset({'OBSERVER'})}, P001.system_node_ids | {tool}, P001.route_edges)
        self.assertEqual(self.relation(ctx, A, (STUDIO, A), tool), 'UNRELATED')

    def test_predecessor_is_direct_only(self):
        # In P002, A -> B -> C: for C, B is a direct predecessor; A is not (G-A6).
        self.assertEqual(self.relation(P002, C, (B, C), B), 'ROUTE_PREDECESSOR')
        self.assertEqual(self.relation(P002, C, (B, C), A), 'PEER')


class P001FixtureTests(unittest.TestCase):
    proto = PROTO['PROTO-P001']

    def test_observer_never_receives_a_peer_answer(self):
        for observer, peer in itertools.permutations((A, B, C), 2):
            d = ev(self.proto, P001, observer, (STUDIO, observer), raw(peer))
            self.assertEqual((d.effect, d.basis, d.deny_rule_ids), ('DENY', 'RULE_DENY', ('no-peer',)), (observer, peer))

    def test_case_input_is_allowed_to_every_observer(self):
        for observer in (A, B, C):
            d = ev(self.proto, P001, observer, (STUDIO, observer), CASE_IN)
            self.assertEqual((d.effect, d.allow_rule_ids, d.relation), ('ALLOW', ('case-input',), 'NONE'))

    def test_own_answer_is_not_resent(self):
        d = ev(self.proto, P001, A, (STUDIO, A), raw(A))
        self.assertEqual((d.effect, d.basis, d.relation), ('DENY', 'DEFAULT_DENY', 'SELF'))

    def test_synthesizer_receives_each_answer_on_its_own_edge_only(self):
        d = ev(self.proto, P001, H, (A, H), raw(A))
        self.assertEqual((d.effect, d.allow_rule_ids), ('ALLOW', ('synth-input',)))
        d = ev(self.proto, P001, H, (B, H), raw(A))  # A's answer riding B's edge: condition D fails
        self.assertEqual((d.effect, d.basis), ('DENY', 'DEFAULT_DENY'))


class P002FixtureTests(unittest.TestCase):
    proto = PROTO['PROTO-P002']

    def test_successor_receives_its_direct_predecessor(self):
        self.assertEqual(ev(self.proto, P002, B, (A, B), raw(A)).effect, 'ALLOW')
        self.assertEqual(ev(self.proto, P002, C, (B, C), raw(B)).effect, 'ALLOW')

    def test_answer_two_steps_back_is_a_peer_and_denied(self):
        d = ev(self.proto, P002, C, (B, C), raw(A))
        self.assertEqual((d.effect, d.basis, d.deny_rule_ids, d.relation), ('DENY', 'RULE_DENY', ('no-peer',), 'PEER'))

    def test_predecessor_answer_cannot_ride_the_case_edge(self):
        d = ev(self.proto, P002, B, (STUDIO, B), raw(A))
        self.assertEqual((d.effect, d.basis, d.relation), ('DENY', 'DEFAULT_DENY', 'ROUTE_PREDECESSOR'))

    def test_case_input_needs_a_system_origin_under_route_edge_exists(self):
        p = protocol(rule('case', kinds=('CASE_INPUT',), relation='NONE', condition='ROUTE_EDGE_EXISTS'))
        self.assertEqual(ev(p, P002, B, (STUDIO, B), CASE_IN).effect, 'ALLOW')
        self.assertEqual(ev(p, P002, B, (A, B), CASE_IN).basis, 'DEFAULT_DENY')


class CombinationTests(unittest.TestCase):
    def test_deny_overrides_allow_and_records_both(self):
        p = protocol(rule('ok'), rule('no', effect='DENY'))
        d = ev(p, P001, A, (STUDIO, A), raw(B))
        self.assertEqual((d.effect, d.basis, d.allow_rule_ids, d.deny_rule_ids), ('DENY', 'RULE_DENY', ('ok',), ('no',)))

    def test_default_deny(self):
        self.assertEqual(ev(protocol(), P001, A, (STUDIO, A), raw(B)).basis, 'DEFAULT_DENY')
        self.assertEqual(ev(protocol(rule('k', kinds=('HUMAN_NOTE',))), P001, A, (STUDIO, A), raw(B)).basis, 'DEFAULT_DENY')

    def test_any_relation_never_matches_unrelated_authors(self):
        p = protocol(rule('everything'))
        for producer in (STUDIO, 'NODE-other-run'):
            d = ev(p, P001, A, (STUDIO, A), ArtifactRef('ART-x-001', 'RAW_RESPONSE', producer))
            self.assertEqual((d.effect, d.basis, d.relation), ('DENY', 'DEFAULT_DENY', 'UNRELATED'))
        self.assertEqual(ev(p, P001, A, (STUDIO, A), raw(B)).effect, 'ALLOW')

    def test_roles_are_matched_as_a_union(self):
        ctx = context('ROUTE-P001-FIX', extra_roles={A: 'SYNTHESIZER'})
        p = protocol(rule('obs-ok', subject={'type': 'ROLE', 'value': 'OBSERVER'}),
                     rule('syn-no', effect='DENY', subject={'type': 'ROLE', 'value': 'SYNTHESIZER'}))
        self.assertEqual(ev(p, ctx, A, (STUDIO, A), raw(B)).deny_rule_ids, ('syn-no',))
        self.assertEqual(ev(p, P001, A, (STUDIO, A), raw(B)).effect, 'ALLOW')  # without the second role

    def test_node_subject(self):
        p = protocol(rule('only-a', subject={'type': 'NODE', 'value': A}))
        self.assertEqual(ev(p, P001, A, (STUDIO, A), raw(B)).effect, 'ALLOW')
        self.assertEqual(ev(p, P001, B, (STUDIO, B), raw(A)).basis, 'DEFAULT_DENY')

    def test_node_value_outside_the_run_just_does_not_match(self):
        p = protocol(rule('ghost', subject={'type': 'NODE', 'value': 'NODE-not-in-run'}))
        self.assertEqual(ev(p, P001, A, (STUDIO, A), raw(B)).basis, 'DEFAULT_DENY')  # E-2: not invalid


class SubmitTests(unittest.TestCase):
    def test_submit_rules_are_listed_as_unevaluated(self):
        d = ev(PROTO['PROTO-P001'], P001, A, (STUDIO, A), CASE_IN)
        self.assertEqual(d.evaluated_action, 'RECEIVE')
        self.assertEqual(d.unevaluated_rule_ids, ('observer-submit',))

    def test_submit_allow_does_not_allow_receiving(self):
        p = protocol(rule('submit-only', action='SUBMIT'))
        d = ev(p, P001, A, (STUDIO, A), raw(B))
        self.assertEqual((d.effect, d.basis, d.unevaluated_rule_ids), ('DENY', 'DEFAULT_DENY', ('submit-only',)))


class ProtocolInvalidTests(unittest.TestCase):
    def assertInvalid(self, proto):
        with self.assertRaises(ProtocolInvalid):
            ev(proto, P001, A, (STUDIO, A), raw(B))

    def test_schema_violations(self):
        bad = copy.deepcopy(PROTO['PROTO-P001'])
        bad['defaultEffect'] = 'ALLOW'
        self.assertInvalid(bad)
        self.assertInvalid(protocol(rule('r', condition="producer == 'x'")))

    def test_meaning_checks_the_schema_cannot_express(self):
        cases = {
            'ROLE without value': rule('r', subject={'type': 'ROLE'}),
            'unknown ROLE': rule('r', subject={'type': 'ROLE', 'value': 'WIZARD'}),
            'NODE without value': rule('r', subject={'type': 'NODE'}),
            'malformed NODE': rule('r', subject={'type': 'NODE', 'value': 'observer-a'}),
            'ANY with value': rule('r', subject={'type': 'ANY', 'value': 'x'}),
        }
        for name, r in cases.items():
            with self.subTest(name):
                self.assertInvalid(protocol(r))

    def test_duplicate_rule_ids_including_submit(self):
        self.assertInvalid(protocol(rule('dup'), rule('dup', effect='DENY')))
        self.assertInvalid(protocol(rule('dup'), rule('dup', action='SUBMIT')))

    def test_invalid_is_not_a_deny(self):
        try:
            result = ev(protocol(rule('r', subject={'type': 'ANY', 'value': 'x'})), P001, A, (STUDIO, A), raw(B))
        except ProtocolInvalid:
            result = None
        self.assertIsNone(result)  # an invalid protocol never produces a Decision


class InputInvalidTests(unittest.TestCase):
    def assertInputInvalid(self, ctx, subject, edge, artifact):
        with self.assertRaises(EvaluationInputInvalid):
            ev(PROTO['PROTO-P001'], ctx, subject, edge, artifact)

    def test_subject_must_be_a_participant(self):
        # The edges are added to the route so that only the subject check can fail.
        stranger = 'NODE-stranger'
        ctx = RunContext(P001.participants, P001.system_node_ids, P001.route_edges | {(STUDIO, stranger), (A, STUDIO)})
        self.assertInputInvalid(ctx, stranger, (STUDIO, stranger), raw(B))
        self.assertInputInvalid(ctx, STUDIO, (A, STUDIO), raw(B))

    def test_dispatch_edge_must_end_at_subject_and_be_in_the_route(self):
        self.assertInputInvalid(P001, A, (STUDIO, B), raw(C))
        self.assertInputInvalid(P001, B, (A, B), raw(C))           # A -> B is not a P001 edge (E-3)
        self.assertInputInvalid(P001, A, ('only-one',), raw(B))

    def test_malformed_artifact(self):
        self.assertInputInvalid(P001, A, (STUDIO, A), ArtifactRef('ART-x-001', 'OPINION', B))
        self.assertInputInvalid(P001, A, (STUDIO, A), ArtifactRef('ART-x-001', 'RAW_RESPONSE', 'observer-b'))

    def test_unknown_participant_role(self):
        ctx = RunContext({**P001.participants, A: frozenset({'WIZARD'})}, P001.system_node_ids, P001.route_edges)
        self.assertInputInvalid(ctx, A, (STUDIO, A), raw(B))

    def test_input_error_is_distinct_from_protocol_error(self):
        with self.assertRaises(EvaluationInputInvalid) as cm:
            ev(PROTO['PROTO-P001'], P001, A, (STUDIO, B), raw(C))
        self.assertNotIsInstance(cm.exception, ProtocolInvalid)


class DeterminismTests(unittest.TestCase):
    def test_same_input_same_decision(self):
        first = ev(PROTO['PROTO-P002'], P002, C, (B, C), raw(A))
        for _ in range(5):
            self.assertEqual(ev(PROTO['PROTO-P002'], P002, C, (B, C), raw(A)), first)
        self.assertIsInstance(first, Decision)

    def test_order_of_context_sets_does_not_matter(self):
        shuffled = RunContext(dict(reversed(list(P002.participants.items()))),
                              frozenset(reversed(sorted(P002.system_node_ids))),
                              frozenset(reversed(sorted(P002.route_edges))))
        for subject, edge, art in ((C, (B, C), raw(A)), (B, (A, B), raw(A)), (B, (STUDIO, B), CASE_IN)):
            self.assertEqual(ev(PROTO['PROTO-P002'], shuffled, subject, edge, art),
                             ev(PROTO['PROTO-P002'], P002, subject, edge, art))

    def test_protocol_and_context_are_not_modified(self):
        proto, before = PROTO['PROTO-P002'], copy.deepcopy(PROTO['PROTO-P002'])
        ev(proto, P002, B, (A, B), raw(A))
        self.assertEqual(proto, before)


if __name__ == '__main__':
    unittest.main()
