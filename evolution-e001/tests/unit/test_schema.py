"""E001-B: schema and validator tests.

Every structural keyword and every semantic rule (SR-01..SR-16) listed in
docs/SCHEMA_VALIDATION_SCOPE.md has at least one test here that proves the
check fires, starting from the valid fixture and changing one thing.
"""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from domain.schema import validator as v
from tests.fixtures import make_fixtures

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / 'tests' / 'fixtures'
SCHEMA = v.load_schema()
VALID = json.loads((FIXTURES / 'p001_bundle_valid.json').read_text(encoding='utf-8'))


def bundle():
    return copy.deepcopy(VALID)


def first(b, entity, **match):
    return next(r for r in b['records'][entity] if all(r.get(k) == val for k, val in match.items()))


class Base(unittest.TestCase):
    def issues(self, b):
        return v.validate_bundle(b, SCHEMA)

    def assertIssue(self, b, code=None, rule=None, path=None):
        found = self.issues(b)
        hit = [i for i in found if (code is None or i.code == code) and (rule is None or i.rule == rule)
               and (path is None or path in i.path)]
        self.assertTrue(hit, f'expected issue code={code} rule={rule} path~{path}; got {found}')
        return hit


class SchemaFileTests(Base):
    def test_schema_uses_only_supported_keywords(self):
        v.load_schema()  # raises SchemaDefinitionError otherwise

    def test_unsupported_keyword_is_rejected_not_ignored(self):
        s = json.loads(v.SCHEMA_PATH.read_text(encoding='utf-8'))
        s['$defs']['case']['oneOf'] = [{'type': 'object'}]
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 's.json'
            p.write_text(json.dumps(s), encoding='utf-8')
            with self.assertRaises(v.SchemaDefinitionError):
                v.load_schema(p)

    def test_unsupported_format_is_rejected(self):
        s = json.loads(v.SCHEMA_PATH.read_text(encoding='utf-8'))
        s['$defs']['timestamp']['format'] = 'email'
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 's.json'
            p.write_text(json.dumps(s), encoding='utf-8')
            with self.assertRaises(v.SchemaDefinitionError):
                v.load_schema(p)

    def test_new_schema_is_distinct_from_reference_schema(self):
        old = json.loads((ROOT / 'docs' / 'reference' / 'AI_Team_Studio_STUDIO_SCHEMA_E001.json').read_text(encoding='utf-8'))
        self.assertNotEqual(old.get('$id'), SCHEMA['$id'])
        self.assertEqual(old['properties']['schemaVersion']['const'], 'E001.0')
        self.assertNotIn('E001.0', v.SUPPORTED_SCHEMA_VERSIONS)

    def test_every_e002_entity_has_a_definition(self):
        for entity in ('case', 'designSpace', 'node', 'provider', 'pattern', 'route', 'trial', 'run', 'runManifest',
                       'source', 'artifact', 'event', 'transfer', 'protocol', 'gate', 'observation', 'synthesis'):
            self.assertIn(entity, SCHEMA['x-entities'])
            self.assertIn(SCHEMA['x-entities'][entity]['def'], SCHEMA['$defs'])


class FixtureTests(Base):
    def test_valid_fixture_has_no_issues(self):
        self.assertEqual(self.issues(bundle()), [])

    def test_fixtures_are_reproducible_from_generator(self):
        for name, build in make_fixtures.FIXTURES.items():
            self.assertEqual(make_fixtures.render(build()), (FIXTURES / name).read_bytes(), name)

    def test_same_core_holds_p001_and_p002(self):
        topologies = {p['topology'] for p in VALID['records']['pattern']}
        self.assertEqual(topologies, {'PARALLEL', 'SEQUENTIAL'})

    def test_isolation_secrets_appear_only_in_own_raw_response(self):
        secrets = json.loads((FIXTURES / 'isolation_secrets.json').read_text(encoding='utf-8'))
        self.assertEqual(sorted(secrets.values()), ['SECRET-A', 'SECRET-B', 'SECRET-C'])
        for art in VALID['records']['artifact']:
            for owner, secret in secrets.items():
                if secret in art['content']:
                    self.assertEqual((art['kind'], art['producerNodeId']), ('RAW_RESPONSE', owner), art['artifactId'])
        for pkt in (a for a in VALID['records']['artifact'] if a['kind'] == 'PACKET'):
            for secret in secrets.values():
                self.assertNotIn(secret, pkt['content'])


class VersionTests(Base):
    def test_unknown_bundle_version_is_rejected_without_further_interpretation(self):
        b = bundle()
        b['schemaVersion'] = 'E001.0'
        found = self.issues(b)
        self.assertEqual([i.code for i in found], ['UNSUPPORTED_SCHEMA_VERSION'])

    def test_missing_bundle_version_is_rejected(self):
        b = bundle()
        del b['schemaVersion']
        self.assertEqual([i.code for i in self.issues(b)], ['UNSUPPORTED_SCHEMA_VERSION'])

    def test_record_with_other_version_is_rejected(self):
        b = bundle()
        b['records']['case'][0]['schemaVersion'] = 'E002/1.0'
        self.assertIssue(b, 'CONST', path='case[0].schemaVersion')
        self.assertEqual(v.validate_record('case', b['records']['case'][0], SCHEMA)[0].code, 'UNSUPPORTED_SCHEMA_VERSION')


class StructureTests(Base):
    def test_every_required_field_is_enforced(self):
        for entity, meta in SCHEMA['x-entities'].items():
            for field in SCHEMA['$defs'][meta['def']]['required']:
                with self.subTest(entity=entity, field=field):
                    b = bundle()
                    del b['records'][entity][0][field]
                    self.assertIssue(b, 'REQUIRED', path=f'{entity}[0].{field}')

    def test_type(self):
        b = bundle()
        b['records']['artifact'][0]['byteSize'] = '12'
        self.assertIssue(b, 'TYPE', path='artifact[0].byteSize')

    def test_boolean_is_not_an_integer(self):
        b = bundle()
        b['records']['event'][0]['sequence'] = True
        self.assertIssue(b, 'TYPE', path='event[0].sequence')

    def test_role_cannot_be_used_as_node_kind(self):
        b = bundle()
        b['records']['node'][2]['kind'] = 'OBSERVER'
        self.assertIssue(b, 'ENUM', path='node[2].kind')

    def test_future_provider_kind_is_rejected(self):
        b = bundle()
        b['records']['provider'][0]['kind'] = 'API'
        self.assertIssue(b, 'ENUM', path='provider[0].kind')

    def test_synthesis_has_no_majority_field(self):
        b = bundle()
        b['records']['synthesis'][0]['majorityVerdict'] = '1859'
        self.assertIssue(b, 'ADDITIONAL', path='synthesis[0].majorityVerdict')

    def test_unknown_record_set_is_rejected(self):
        b = bundle()
        b['records']['experience'] = []
        self.assertIssue(b, 'ADDITIONAL', path='records.experience')

    def test_id_prefix_pattern(self):
        b = bundle()
        b['records']['case'][0]['caseId'] = 'CASE-20261008-001'  # valid
        b['records']['node'][0]['nodeId'] = 'studio'
        self.assertIssue(b, 'PATTERN', path='node[0].nodeId')

    def test_timestamp_requires_timezone(self):
        b = bundle()
        b['records']['case'][0]['createdAt'] = '2026-10-08T09:00:00'
        self.assertIssue(b, 'FORMAT', path='case[0].createdAt')

    def test_applied_experience_must_be_empty_in_e001(self):
        b = bundle()
        b['records']['trial'][0]['appliedExperienceIds'] = ['EXP-1']
        self.assertIssue(b, 'MAX_ITEMS', path='trial[0].appliedExperienceIds')

    def test_protocol_default_must_be_deny(self):
        b = bundle()
        b['records']['protocol'][0]['defaultEffect'] = 'ALLOW'
        self.assertIssue(b, 'CONST', path='protocol[0].defaultEffect')

    def test_protocol_condition_is_a_closed_list(self):
        b = bundle()
        b['records']['protocol'][0]['rules'][0]['condition'] = "node.name == 'GPT'"
        self.assertIssue(b, 'ENUM', path='rules[0].condition')

    def test_min_items(self):
        b = bundle()
        b['records']['synthesis'][0]['inputObservationIds'] = []
        self.assertIssue(b, 'MIN_ITEMS', path='synthesis[0].inputObservationIds')

    def test_minimum(self):
        b = bundle()
        b['records']['trial'][0]['budget']['maxRuns'] = 0
        self.assertIssue(b, 'MINIMUM', path='trial[0].budget.maxRuns')

    def test_validate_record_structural_only(self):
        self.assertEqual(v.validate_record('artifact', VALID['records']['artifact'][0], SCHEMA), [])
        self.assertEqual(v.validate_record('experience', {}, SCHEMA)[0].code, 'UNKNOWN_ENTITY')


class ReferenceTests(Base):
    def test_missing_reference(self):
        b = bundle()
        b['records']['observation'][0]['transferId'] = 'XFER-does-not-exist'
        self.assertIssue(b, 'REF', path='observation[0].transferId')

    def test_nullable_reference_checked_when_set(self):
        b = bundle()
        b['records']['run'][0]['retryOfRunId'] = 'RUN-missing-1'
        self.assertIssue(b, 'REF', path='run[0].retryOfRunId')

    def test_versioned_reference(self):
        b = bundle()
        b['records']['trial'][0]['patternRef']['version'] = '2.0'
        self.assertIssue(b, 'VERSIONED_REF', path='trial[0].patternRef')

    def test_duplicate_artifact_id(self):
        b = bundle()
        dup = copy.deepcopy(b['records']['artifact'][-1])
        b['records']['artifact'].append(dup)
        self.assertIssue(b, 'DUPLICATE_ID', 'SR-01')

    def test_versioned_entities_may_share_id_across_versions_only(self):
        b = bundle()
        p = copy.deepcopy(b['records']['pattern'][0])
        p['version'] = '1.1'
        b['records']['pattern'].append(p)
        self.assertEqual(self.issues(b), [])
        b['records']['pattern'].append(copy.deepcopy(p))
        self.assertIssue(b, 'DUPLICATE_ID', 'SR-01')


class SemanticRuleTests(Base):
    def test_sr02_manual_provider_cannot_claim_enforced(self):
        b = bundle()
        b['records']['provider'][0]['maxIsolationAssurance'] = 'ENFORCED'
        self.assertIssue(b, rule='SR-02', path='provider[0]')

    def test_sr02_manual_transfer_cannot_claim_enforced(self):
        b = bundle()
        b['records']['transfer'][0]['isolationAssurance'] = 'ENFORCED'
        self.assertIssue(b, rule='SR-02', path='transfer[0]')

    def test_sr03_verified_requires_verified_hash(self):
        b = bundle()
        b['records']['source'][0]['verifiedHash'] = None
        self.assertIssue(b, rule='SR-03', path='source[0]')

    def test_sr03_mismatch_requires_differing_hashes(self):
        b = bundle()
        s = b['records']['source'][0]
        s['claimedHash'] = s['verifiedHash']
        s['verificationStatus'] = 'MISMATCH'
        self.assertIssue(b, rule='SR-03', path='source[0]')

    def test_sr03_unverified_cannot_hold_verified_hash(self):
        b = bundle()
        b['records']['runManifest'][0]['sources'][0]['verificationStatus'] = 'UNVERIFIED'
        self.assertIssue(b, rule='SR-03', path='runManifest[0].sources[0]')

    def test_sr04_content_change_breaks_hash(self):
        b = bundle()
        a = first(b, 'artifact', artifactId='ART-raw-a')
        a['content'] = a['content'].replace('1859', '1858')
        self.assertIssue(b, 'HASH_MISMATCH', 'SR-04')

    def test_sr04_hash_is_over_exact_bytes_without_newline_normalization(self):
        b = bundle()
        a = first(b, 'artifact', artifactId='ART-raw-b')
        self.assertIn('\r\n', a['content'])
        a['content'] = a['content'].replace('\r\n', '\n')
        a['byteSize'] = len(a['content'].encode('utf-8'))
        self.assertIssue(b, 'HASH_MISMATCH', 'SR-04', path='contentHash')

    def test_sr04_byte_size(self):
        b = bundle()
        b['records']['artifact'][0]['byteSize'] += 1
        self.assertIssue(b, 'HASH_MISMATCH', 'SR-04', path='byteSize')

    def test_sr04_exactly_one_content_location(self):
        b = bundle()
        b['records']['artifact'][0]['contentLocation'] = 'artifacts/x.bin'
        self.assertIssue(b, rule='SR-04', path='artifact[0]')
        b = bundle()
        del b['records']['artifact'][0]['content']
        self.assertIssue(b, rule='SR-04', path='artifact[0]')

    def test_sr05_packet_hash_must_match_packet(self):
        b = bundle()
        b['records']['transfer'][0]['packetHash'] = '0' * 64
        self.assertIssue(b, 'HASH_MISMATCH', 'SR-05')

    def test_sr05_packet_must_be_packet_kind(self):
        b = bundle()
        t = b['records']['transfer'][0]
        raw = first(b, 'artifact', artifactId='ART-raw-a')
        t['packetArtifactId'], t['packetHash'] = raw['artifactId'], raw['contentHash']
        self.assertIssue(b, rule='SR-05', path='packetArtifactId')

    def test_sr06_observer_must_be_transfer_destination(self):
        b = bundle()
        b['records']['observation'][0]['observerNodeId'] = 'NODE-observer-b'
        self.assertIssue(b, rule='SR-06', path='observerNodeId')

    def test_sr06_raw_must_be_raw_response(self):
        b = bundle()
        b['records']['observation'][0]['rawResponseArtifactId'] = 'ART-packet-a'
        self.assertIssue(b, rule='SR-06', path='rawResponseArtifactId')

    def test_sr07_synthesis_cites_only_its_inputs(self):
        b = bundle()
        s = b['records']['synthesis'][0]
        s['inputObservationIds'] = ['OBS-fix-a', 'OBS-fix-b']
        self.assertIssue(b, rule='SR-07', path='contradictions[0]')

    def test_sr08_event_sequence_strictly_increasing(self):
        b = bundle()
        b['records']['event'][3]['sequence'] = 3
        self.assertIssue(b, rule='SR-08', path='event[3]')

    def test_sr09_gate_decided_by_human(self):
        b = bundle()
        b['records']['gate'][0]['decidedBy'] = 'NODE-observer-a'
        self.assertIssue(b, rule='SR-09')

    def test_sr10_run_and_manifest_match(self):
        b = bundle()
        b['records']['runManifest'][0]['runId'] = 'RUN-other-1'
        b['records']['run'].append(dict(copy.deepcopy(b['records']['run'][0]), runId='RUN-other-1'))
        self.assertIssue(b, rule='SR-10', path='run[0].runManifestId')

    def test_sr11_bindings_cover_slots(self):
        b = bundle()
        b['records']['route'][0]['slotBindings'].pop()
        self.assertIssue(b, rule='SR-11', path='route[0].slotBindings')

    def test_sr11_edge_endpoint_must_be_bound_or_system(self):
        b = bundle()
        b['records']['node'].append(dict(copy.deepcopy(b['records']['node'][2]), nodeId='NODE-stranger'))
        b['records']['route'][0]['edges'][0]['toNodeId'] = 'NODE-stranger'
        self.assertIssue(b, rule='SR-11', path='edges[0].toNodeId')

    def test_sr12_transfer_follows_route_edge(self):
        b = bundle()
        b['records']['transfer'][0]['edgeId'] = 'a-to-b'  # an edge of the P002 route, not this run's route
        self.assertIssue(b, rule='SR-12', path='transfer[0].edgeId')

    def test_sr12_transfer_endpoints_match_edge(self):
        b = bundle()
        b['records']['transfer'][0]['edgeId'] = 'case-b'
        self.assertIssue(b, rule='SR-12', path='transfer[0]')

    def test_sr13_manifest_role_matches_pattern(self):
        b = bundle()
        b['records']['runManifest'][0]['roleAssignments'][0]['role'] = 'SYNTHESIZER'
        self.assertIssue(b, rule='SR-13', path='roleAssignments[0]')

    def test_sr14_l1_evaluation_by_system_only(self):
        b = bundle()
        b['records']['evaluation'][0]['evaluatorNodeId'] = 'NODE-observer-a'
        self.assertIssue(b, rule='SR-14')

    def test_sr15_trial_and_route_agree(self):
        b = bundle()
        b['records']['trial'][0]['routeRef'] = {'id': 'ROUTE-P002-FIX', 'version': '1.0'}
        self.assertIssue(b, rule='SR-15', path='trial[0].patternRef')

    def test_sr16_gate_target_must_exist(self):
        b = bundle()
        b['records']['gate'][0]['targetId'] = 'SYN-missing-1'
        self.assertIssue(b, rule='SR-16', path='gate[0].targetId')

    def test_sr16_gate_target_type_and_id_must_match(self):
        b = bundle()
        b['records']['gate'][0]['targetType'] = 'observation'  # SYN- id under another type
        self.assertIssue(b, rule='SR-16', path='gate[0].targetId')


class ExecutionContextRuleTests(Base):
    """E001-D semantic rules SR-17..SR-21 (validator only; the schema JSON is unchanged)."""

    def test_sr17_artifact_run_must_match_its_transfer(self):
        b = bundle()
        b['records']['run'].append(dict(copy.deepcopy(b['records']['run'][0]), runId='RUN-FIX-P001-2', runManifestId='MAN-FIX-P001-1'))
        first(b, 'artifact', artifactId='ART-raw-a')['provenance']['runId'] = 'RUN-FIX-P001-2'
        self.assertIssue(b, rule='SR-17', path='provenance.runId')

    def test_sr17_artifact_case_must_match_its_trial(self):
        b = bundle()
        b['records']['case'].append(dict(copy.deepcopy(b['records']['case'][0]), caseId='CASE-FIX-002'))
        a = first(b, 'artifact', artifactId='ART-raw-a')
        a['provenance']['caseId'] = 'CASE-FIX-002'
        self.assertIssue(b, rule='SR-17', path='provenance.caseId')

    def test_sr18_transfer_cannot_exceed_provider_ceiling(self):
        b = bundle()
        b['records']['provider'][0]['maxIsolationAssurance'] = 'DECLARED'
        b['records']['runManifest'][0]['providers'][0]['maxIsolationAssurance'] = 'DECLARED'
        self.assertIssue(b, rule='SR-18', path='transfer[0].isolationAssurance')

    def test_sr19_runs_within_budget(self):
        b = bundle()
        b['records']['trial'][0]['budget']['maxRuns'] = 1
        b['records']['run'].append(dict(copy.deepcopy(b['records']['run'][0]), runId='RUN-FIX-P001-2'))
        self.assertIssue(b, rule='SR-19', path='trial[0].budget.maxRuns')

    def test_sr20_retry_stays_in_its_trial(self):
        b = bundle()
        b['records']['run'].append(dict(copy.deepcopy(b['records']['run'][0]), runId='RUN-FIX-P002-1',
                                        trialId='TRIAL-FIX-P002', retryOfRunId='RUN-FIX-P001-1'))
        self.assertIssue(b, rule='SR-20', path='run[1].retryOfRunId')

    def test_sr21_system_actor_is_system(self):
        b = bundle()
        first(b, 'node', nodeId='NODE-studio')['kind'] = 'HUMAN'
        self.assertIssue(b, rule='SR-21', path='node')

    def test_sr21_system_actor_never_fills_a_role(self):
        b = bundle()
        b['records']['route'][0]['slotBindings'][0]['nodeId'] = 'NODE-studio'
        b['records']['runManifest'][0]['roleAssignments'][0]['nodeId'] = 'NODE-studio'
        self.assertIssue(b, rule='SR-21', path='route[0].slotBindings[0]')
        self.assertIssue(b, rule='SR-21', path='runManifest[0].roleAssignments[0]')


if __name__ == '__main__':
    unittest.main()
