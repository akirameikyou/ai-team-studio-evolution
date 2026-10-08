"""E001-C: Artifact <-> Event linkage, event sequence, reserved system actor, bundle provenance."""
import hashlib
import json
from unittest import mock

from domain.schema import validator
from infrastructure.storage.event_log import SYSTEM_ACTOR_ID, EventSequenceCollision
from tests.persistence.support import CASE, StoreTestCase, remove, snapshot, tamper

V = 'E001/1.0'


class EventLinkTests(StoreTestCase):
    def test_artifact_received_event_records_both_hashes(self):
        a = self.put(b'answer', run_id='RUN-T-001')
        events, problems = self.store.events.read_all()
        self.assertEqual(problems, [])
        (ev,) = events
        meta_bytes = (self.adir(a.artifact_id) / 'artifact.json').read_bytes()
        self.assertEqual((ev['eventType'], ev['targetType'], ev['targetId'], ev['artifactRefs']),
                         ('ARTIFACT_RECEIVED', 'artifact', a.artifact_id, [a.artifact_id]))
        self.assertEqual(ev['payload']['artifactHash'], hashlib.sha256(b'answer').hexdigest())
        self.assertEqual(ev['payload']['metadataHash'], hashlib.sha256(meta_bytes).hexdigest())
        self.assertEqual((ev['payload']['caseId'], ev['payload']['runId'], ev['payload']['via']), (CASE, 'RUN-T-001', 'MANUAL_PASTE'))

    def test_events_are_recorded_by_the_reserved_system_actor(self):
        self.put(b'x')
        (ev,) = self.store.events.read_all()[0]
        self.assertEqual(SYSTEM_ACTOR_ID, 'NODE-studio')
        self.assertEqual(ev['actorNodeId'], SYSTEM_ACTOR_ID)

    def test_sequence_is_contiguous_and_matches_file_names(self):
        for i in range(3):
            self.put(f'answer {i}'.encode())
        events, problems = self.store.events.read_all()
        self.assertEqual(problems, [])
        self.assertEqual([e['sequence'] for e in events], [1, 2, 3])
        self.assertEqual(sorted(p.name for p in self.store.events.dir.iterdir()),
                         ['0000000001.json', '0000000002.json', '0000000003.json'])

    def test_sequence_collision_is_rejected_and_existing_event_untouched(self):
        self.put(b'first')
        before = snapshot(self.store.events.dir)
        with mock.patch.object(self.store.events, '_next_sequence', return_value=1):
            with self.assertRaises(EventSequenceCollision):
                self.store.events.append(event_type='ARTIFACT_RECEIVED', target_type='artifact', target_id='ART-x-001', payload={})
        self.assertEqual(snapshot(self.store.events.dir), before)

    def test_edited_event_breaks_the_link(self):
        a = self.put(b'answer')
        path = self.store.events.dir / '0000000001.json'
        ev = json.loads(path.read_bytes())
        ev['payload']['metadataHash'] = '0' * 64
        tamper(path, json.dumps(ev).encode())
        self.assertIn('METADATA_MISMATCH', self.codes(self.store.verify()))
        self.assertEqual(self.store.inspect(a.artifact_id).status, 'CORRUPT')

    def test_unreadable_event_is_reported_and_its_artifact_becomes_orphan(self):
        a = self.put(b'answer')
        tamper(self.store.events.dir / '0000000001.json', b'{broken')
        codes = self.codes(self.store.verify())
        self.assertIn('BAD_EVENT_FILE', codes)
        self.assertIn('ORPHAN', codes)
        self.assertFalse(self.store.read(a.artifact_id).event_linked)

    def test_missing_event_file_is_a_sequence_gap(self):
        for i in range(3):
            self.put(f'answer {i}'.encode())
        remove(self.store.events.dir / '0000000002.json')
        self.assertIn('SEQUENCE_GAP', self.codes(self.store.verify()))

    def test_event_file_with_wrong_sequence_is_reported(self):
        self.put(b'answer')
        src = self.store.events.dir / '0000000001.json'
        tamper(self.store.events.dir / '0000000002.json', src.read_bytes())  # same event under another number
        self.assertIn('SEQUENCE_MISMATCH', self.codes(self.store.verify()))


class BundleProvenanceTests(StoreTestCase):
    """Reference existence is checked by validate_bundle (I-7), not at create time."""

    def bundle(self, extra=None):
        records = self.store.bundle_records()
        records['case'] = [{'schemaVersion': V, 'caseId': CASE, 'question': 'q', 'intent': '', 'constraints': [],
                            'createdAt': '2026-10-08T09:00:00Z', 'status': 'OPEN'}]
        records['node'] = [{'schemaVersion': V, 'nodeId': SYSTEM_ACTOR_ID, 'kind': 'SYSTEM', 'displayName': 'Studio',
                            'createdAt': '2026-10-08T09:00:00Z'}]
        records.update(extra or {})
        return {'schemaVersion': V, 'bundleType': 'E001_STORE_BUNDLE', 'records': records}

    def test_stored_records_form_a_valid_bundle(self):
        self.put(b'answer 1\r\n')
        self.put(b'source text', kind='SOURCE_CONTENT', source_kind='USER', received_via='MANUAL_FILE')
        self.assertEqual(validator.validate_bundle(self.bundle()), [])

    def test_bundle_reveals_a_missing_run(self):
        self.put(b'answer', run_id='RUN-missing-1')
        issues = validator.validate_bundle(self.bundle())
        self.assertIn('REF', {i.code for i in issues})

    def test_bundle_requires_the_system_actor_node(self):
        self.put(b'answer')
        b = self.bundle()
        b['records']['node'] = []
        self.assertIn('REF', {i.code for i in validator.validate_bundle(b)})
