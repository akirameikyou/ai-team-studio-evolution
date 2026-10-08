"""E001-D: RecordStore — write-once persistence for schema records (mechanism only)."""
import json
import stat

from infrastructure.storage.record_store import (DuplicateRecord, InvalidRecord, RecordIntegrityError, RecordNotFound,
                                                 RecordStore, RecordStoreError, UnknownEntity)
from tests.persistence.support import StoreTestCase, snapshot, tamper

V = 'E001/1.0'
CASE = {'schemaVersion': V, 'caseId': 'CASE-rs-001', 'question': 'q', 'intent': '', 'constraints': [],
        'createdAt': '2026-10-08T09:00:00Z', 'status': 'OPEN'}
PATTERN = {'schemaVersion': V, 'patternId': 'PAT-rs1', 'version': '1.0', 'name': 'p', 'topology': 'PARALLEL',
           'slots': [{'slotId': 'a', 'role': 'OBSERVER'}]}


class RecordStoreTests(StoreTestCase):
    def setUp(self):
        super().setUp()
        self.rs = RecordStore(self.store.root)

    def test_requires_an_initialized_store(self):
        with self.assertRaises(RecordStoreError):
            RecordStore(self.tmp / 'nowhere')

    def test_put_and_get_round_trip_with_hash(self):
        stored = self.rs.put('case', CASE)
        again = self.rs.get('case', 'CASE-rs-001')
        self.assertEqual(again.record(), CASE)
        self.assertEqual(again.record_hash, stored.record_hash)
        self.assertEqual(self.rs.list('case'), ['CASE-rs-001'])

    def test_duplicate_is_rejected_and_original_untouched(self):
        self.rs.put('case', CASE)
        before = snapshot(self.rs.dir)
        with self.assertRaises(DuplicateRecord):
            self.rs.put('case', dict(CASE, question='changed'))
        self.assertEqual(snapshot(self.rs.dir), before)

    def test_versions_are_separate_records(self):
        self.rs.put('pattern', PATTERN)
        self.rs.put('pattern', dict(PATTERN, version='1.1'))
        self.assertEqual(self.rs.list('pattern'), [('PAT-rs1', '1.0'), ('PAT-rs1', '1.1')])
        with self.assertRaises(DuplicateRecord):
            self.rs.put('pattern', dict(PATTERN, name='other'))
        with self.assertRaises(RecordNotFound):
            self.rs.get('pattern', 'PAT-rs1')  # versioned entities need a version

    def test_invalid_record_writes_nothing(self):
        before = snapshot(self.rs.dir)
        with self.assertRaises(InvalidRecord):
            self.rs.put('case', dict(CASE, status='DONE'))
        self.assertEqual(snapshot(self.rs.dir), before)

    def test_artifacts_and_events_are_not_record_store_entities(self):
        for entity in ('artifact', 'event', 'experience'):
            with self.assertRaises(UnknownEntity):
                self.rs.put(entity, {})

    def test_ids_cannot_escape_the_records_directory(self):
        # The traversal target exists, so only the id guard (not a missing file) can stop the read.
        self.rs.put('node', {'schemaVersion': V, 'nodeId': 'NODE-rs-001', 'kind': 'HUMAN', 'displayName': 'n',
                             'createdAt': '2026-10-08T09:00:00Z'})
        self.assertTrue((self.rs.dir / 'case' / '..' / 'node' / 'NODE-rs-001.json').resolve().is_file())
        for bad in ('../node/NODE-rs-001', '..\\node\\NODE-rs-001', '../store', 'CASE-../../x', 'C:/x', 'case'):
            with self.assertRaises(RecordNotFound):
                self.rs.get('case', bad)
        with self.assertRaises(RecordNotFound):
            self.rs.get('pattern', 'PAT-rs1', '../1.0')

    def test_returned_record_is_a_copy(self):
        self.rs.put('case', CASE)
        rec = self.rs.get('case', 'CASE-rs-001').record()
        rec['question'] = 'mutated'
        self.assertEqual(self.rs.get('case', 'CASE-rs-001').record()['question'], 'q')

    def test_record_file_is_read_only(self):
        self.rs.put('case', CASE)
        path = self.rs.dir / 'case' / 'CASE-rs-001.json'
        self.assertFalse(path.stat().st_mode & stat.S_IWRITE)

    def test_tampered_records_are_detected(self):
        self.rs.put('case', CASE)
        self.rs.put('pattern', PATTERN)
        case_path = self.rs.dir / 'case' / 'CASE-rs-001.json'
        for data, code in ((b'{oops', 'INVALID_RECORD'),
                           (json.dumps(dict(CASE, status='DONE')).encode(), 'INVALID_RECORD'),
                           (json.dumps(dict(CASE, caseId='CASE-rs-999')).encode(), 'ID_MISMATCH')):
            with self.subTest(code=code):
                tamper(case_path, data)
                with self.assertRaises(RecordIntegrityError) as cm:
                    self.rs.get('case', 'CASE-rs-001')
                self.assertIn(code, {p.code for p in cm.exception.problems})
                self.assertIn(code, self.codes(self.rs.verify()))
        tamper(self.rs.dir / 'pattern' / 'PAT-rs1' / '1.0.json', json.dumps(dict(PATTERN, version='2.0')).encode())
        self.assertIn('VERSION_MISMATCH', self.codes(self.rs.verify()))

    def test_unexpected_files_are_reported_not_removed(self):
        self.rs.put('case', CASE)
        tamper(self.rs.dir / 'case' / '.CASE-rs-001.json.abc.tmp', b'partial')
        tamper(self.rs.dir / 'notes.txt', b'x')
        self.assertEqual(self.codes(self.rs.verify()), {'UNEXPECTED_FILE'})
        self.assertTrue((self.rs.dir / 'notes.txt').exists())
