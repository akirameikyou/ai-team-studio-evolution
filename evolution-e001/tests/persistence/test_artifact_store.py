"""E001-C: write-once Artifact store — create, read, exact-byte hashing, write-once, corruption.

Test letters A-G follow the E001-C Human Gate (A create, B read, C hash, D duplicate ID,
E overwrite, F mutation, G corruption).
"""
import dataclasses
import hashlib
import json
import stat
from unittest import mock

from infrastructure.storage import artifact_store as store_mod
from infrastructure.storage.artifact_store import (ArtifactIntegrityError, ArtifactNotFound, ArtifactStore,
                                                   DuplicateArtifactId, InvalidArtifact, UnsupportedContent,
                                                   UnsupportedStore)
from infrastructure.storage.integrity import create_exclusive
from tests.persistence.support import CASE, StoreTestCase, remove, snapshot, tamper

SAMPLES = {
    'crlf': 'line one\r\nline two\r\n'.encode('utf-8'),
    'lf': 'line one\nline two\n'.encode('utf-8'),
    'bom': b'\xef\xbb\xbfwith BOM\n',
    'trailing_space': b'value   \n\n',
    'japanese': '日本語の回答。SECRET-A\n'.encode('utf-8'),
    'empty': b'',
}


class CreateAndReadTests(StoreTestCase):
    def test_A_create_writes_payload_metadata_and_one_event(self):
        a = self.put(b'hello')
        d = self.adir(a.artifact_id)
        self.assertEqual(sorted(p.name for p in d.iterdir()), ['artifact.json', 'payload.bin'])
        self.assertEqual(len(self.store.events.read_all()[0]), 1)
        self.assertEqual(self.store.inspect(a.artifact_id).status, 'OK')
        self.assertEqual(self.store.verify(), [])

    def test_B_read_returns_exact_bytes(self):
        for name, data in SAMPLES.items():
            with self.subTest(name):
                a = self.put(data)
                self.assertEqual(self.store.read(a.artifact_id).content, data)
                self.assertEqual((self.adir(a.artifact_id) / 'payload.bin').read_bytes(), data)

    def test_C_hash_is_sha256_of_exact_stored_bytes(self):
        hashes = {}
        for name, data in SAMPLES.items():
            a = self.put(data)
            stored = (self.adir(a.artifact_id) / 'payload.bin').read_bytes()
            self.assertEqual(a.content_hash, hashlib.sha256(stored).hexdigest(), name)
            self.assertEqual(a.metadata()['byteSize'], len(data), name)
            hashes[name] = a.content_hash
        self.assertNotEqual(hashes['crlf'], hashes['lf'])  # no newline normalization

    def test_text_must_be_bytes(self):
        with self.assertRaises(TypeError):
            self.store.create(content='text', kind='RAW_RESPONSE', case_id=CASE, source_kind='AI', received_via='MANUAL_PASTE')

    def test_non_utf8_rejected_before_anything_is_written(self):
        before = snapshot(self.store.root)
        with self.assertRaises(UnsupportedContent):
            self.put(b'\xff\xfe binary')
        self.assertEqual(snapshot(self.store.root), before)

    def test_invalid_record_rejected_before_anything_is_written(self):
        before = snapshot(self.store.root)
        with self.assertRaises(InvalidArtifact):
            self.put(kind='MAJORITY_TRUTH')
        with self.assertRaises(InvalidArtifact):
            self.put(case_id='not-a-case')
        self.assertEqual(snapshot(self.store.root), before)

    def test_manual_provider_provenance_is_recorded(self):
        a = self.put(b'pasted by a human', run_id='RUN-T-001', transfer_id='XFER-T-001', producer_node_id='NODE-observer-a')
        p = a.metadata()['provenance']
        self.assertEqual((p['sourceKind'], p['receivedVia'], p['caseId'], p['runId'], p['transferId']),
                         ('AI', 'MANUAL_PASTE', CASE, 'RUN-T-001', 'XFER-T-001'))
        f = self.put(b'from a file', source_kind='USER', received_via='MANUAL_FILE', kind='SOURCE_CONTENT')
        self.assertEqual(f.metadata()['provenance']['receivedVia'], 'MANUAL_FILE')

    def test_same_content_twice_gives_two_artifacts(self):
        a, b = self.put(b'same'), self.put(b'same')
        self.assertNotEqual(a.artifact_id, b.artifact_id)
        self.assertEqual(a.content_hash, b.content_hash)  # identity and content hash are separate
        self.assertEqual(len(self.store.events.read_all()[0]), 2)

    def test_existing_store_is_reopened_and_unknown_dirs_are_refused(self):
        a = self.put(b'x')
        self.assertEqual(ArtifactStore(self.store.root).read(a.artifact_id).content, b'x')
        (self.tmp / 'other').mkdir()
        (self.tmp / 'other' / 'notes.txt').write_bytes(b'keep me')
        with self.assertRaises(UnsupportedStore):
            ArtifactStore(self.tmp / 'other')
        self.assertEqual((self.tmp / 'other' / 'notes.txt').read_bytes(), b'keep me')


class WriteOnceTests(StoreTestCase):
    def test_D_duplicate_id_is_rejected_and_original_is_untouched(self):
        a = self.put(b'original', artifact_id='ART-fixed-001')
        before = snapshot(self.store.root)
        with self.assertRaises(DuplicateArtifactId):
            self.put(b'replacement', artifact_id='ART-fixed-001')
        self.assertEqual(snapshot(self.store.root), before)
        self.assertEqual(self.store.read(a.artifact_id).content, b'original')

    def test_E_store_has_no_overwrite_update_or_delete_operation(self):
        public = {n for n in dir(ArtifactStore) if not n.startswith('_')}
        self.assertEqual(public, {'create', 'read', 'inspect', 'list_ids', 'verify', 'export_artifacts',
                                  'import_artifacts', 'bundle_records'})

    def test_E_exclusive_create_never_replaces_a_file(self):
        a = self.put(b'original')
        payload = self.adir(a.artifact_id) / 'payload.bin'
        with self.assertRaises(FileExistsError):
            create_exclusive(payload, b'replacement')
        self.assertEqual(payload.read_bytes(), b'original')
        self.assertEqual(sorted(p.name for p in payload.parent.iterdir()), ['artifact.json', 'payload.bin'])

    def test_F_returned_artifact_cannot_be_mutated(self):
        a = self.put(b'original')
        with self.assertRaises(dataclasses.FrozenInstanceError):
            a.content = b'changed'
        meta = a.metadata()
        meta['provenance']['caseId'] = 'CASE-other-1'
        meta['contentHash'] = '0' * 64
        again = self.store.read(a.artifact_id)
        self.assertEqual(again.metadata()['provenance']['caseId'], CASE)
        self.assertEqual(again.metadata_bytes, a.metadata_bytes)

    def test_committed_files_carry_the_read_only_attribute(self):
        # Defense in depth against accidental writes; not tamper resistance.
        a = self.put(b'original')
        for name in ('payload.bin', 'artifact.json'):
            path = self.adir(a.artifact_id) / name
            self.assertFalse(path.stat().st_mode & stat.S_IWRITE, name)
            with self.assertRaises(PermissionError):
                path.open('wb')

    def test_id_cannot_escape_the_store(self):
        with self.assertRaises(InvalidArtifact):
            self.put(artifact_id='ART-../../escape')
        self.assertFalse((self.tmp / 'escape').exists())
        for bad in ('../store.json', 'ART-x/../../store.json', 'C:/Windows'):
            with self.assertRaises(ArtifactNotFound):
                self.store.read(bad)


class IncompleteAndOrphanTests(StoreTestCase):
    def test_incomplete_artifact_is_detected_and_its_id_stays_reserved(self):
        real = store_mod.create_exclusive

        def fail_on_metadata(path, data, read_only=True):
            if path.name == 'artifact.json':
                raise OSError('simulated crash before commit')
            return real(path, data, read_only)

        with mock.patch.object(store_mod, 'create_exclusive', fail_on_metadata):
            with self.assertRaises(OSError):
                self.put(b'half written', artifact_id='ART-half-001')
        self.assertEqual(self.store.inspect('ART-half-001').status, 'INCOMPLETE')
        self.assertIn('INCOMPLETE', self.codes(self.store.verify()))
        with self.assertRaises(ArtifactIntegrityError):
            self.store.read('ART-half-001')
        with self.assertRaises(DuplicateArtifactId):
            self.put(b'second try', artifact_id='ART-half-001')

    def test_orphan_artifact_is_kept_visible_not_hidden(self):
        with mock.patch.object(self.store.events, 'append', side_effect=OSError('simulated crash before event')):
            with self.assertRaises(OSError):
                self.put(b'no event', artifact_id='ART-orphan-001')
        self.assertEqual(self.store.inspect('ART-orphan-001').status, 'ORPHAN')
        self.assertIn('ORPHAN', self.codes(self.store.verify()))
        art = self.store.read('ART-orphan-001')
        self.assertFalse(art.event_linked)
        self.assertEqual(art.content, b'no event')

    def test_leftover_temporary_file_is_reported(self):
        a = self.put(b'x')
        d = self.adir(a.artifact_id)
        tamper(d / '.payload.bin.deadbeef.tmp', b'partial')
        self.assertIn('UNEXPECTED_FILE', self.codes(self.store.verify()))
        with self.assertRaises(ArtifactIntegrityError):
            self.store.read(a.artifact_id)

    def test_event_naming_a_missing_artifact_is_reported(self):
        a = self.put(b'x')
        d = self.adir(a.artifact_id)
        for p in list(d.iterdir()):
            remove(p)
        d.rmdir()
        self.assertIn('EVENT_WITHOUT_ARTIFACT', self.codes(self.store.verify()))


class CorruptionTests(StoreTestCase):
    def setUp(self):
        super().setUp()
        self.a = self.put('original answer: 1859\r\n'.encode('utf-8'))
        self.d = self.adir(self.a.artifact_id)

    def assertBlocked(self, *expected):
        with self.assertRaises(ArtifactIntegrityError) as cm:
            self.store.read(self.a.artifact_id)
        found = {p.code for p in cm.exception.problems}
        self.assertTrue(found & set(expected), f'expected one of {expected}, got {found}')
        self.assertTrue(set(expected) & self.codes(self.store.verify()))
        self.assertEqual(self.store.inspect(self.a.artifact_id).status, 'CORRUPT')

    def meta(self):
        return json.loads((self.d / 'artifact.json').read_bytes().decode('utf-8'))

    def write_meta(self, meta):
        tamper(self.d / 'artifact.json', (json.dumps(meta, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))

    def test_G_payload_one_byte_changed(self):
        data = bytearray((self.d / 'payload.bin').read_bytes())
        data[-3] ^= 0x01
        tamper(self.d / 'payload.bin', bytes(data))
        self.assertBlocked('HASH_MISMATCH')

    def test_G_payload_newline_normalized(self):
        tamper(self.d / 'payload.bin', (self.d / 'payload.bin').read_bytes().replace(b'\r\n', b'\n'))
        self.assertBlocked('HASH_MISMATCH', 'SIZE_MISMATCH')

    def test_G_payload_missing(self):
        remove(self.d / 'payload.bin')
        self.assertBlocked('MISSING_PAYLOAD')

    def test_G_metadata_provenance_edited(self):
        m = self.meta()
        m['provenance']['caseId'] = 'CASE-other-1'  # still schema-valid
        self.write_meta(m)
        self.assertBlocked('METADATA_MISMATCH')

    def test_G_payload_and_hash_forged_consistently(self):
        forged = b'forged answer: 1858\r\n'
        tamper(self.d / 'payload.bin', forged)
        m = self.meta()
        m['contentHash'], m['byteSize'] = hashlib.sha256(forged).hexdigest(), len(forged)
        self.write_meta(m)
        self.assertBlocked('EVENT_HASH_MISMATCH', 'METADATA_MISMATCH')

    def test_G_metadata_unreadable(self):
        tamper(self.d / 'artifact.json', b'{ not json')
        self.assertBlocked('INVALID_METADATA')

    def test_G_artifact_id_edited(self):
        m = self.meta()
        m['artifactId'] = 'ART-someone-else'
        self.write_meta(m)
        self.assertBlocked('ID_MISMATCH', 'BAD_LOCATION')


class ContentLocationTests(StoreTestCase):
    """contentLocation is never followed: it must equal artifacts/<own id>/payload.bin."""

    def setUp(self):
        super().setUp()
        self.a = self.put(b'answer A')
        self.b = self.put(b'answer B')
        self.d = self.adir(self.a.artifact_id)

    def check(self, location, expected_codes):
        m = json.loads((self.d / 'artifact.json').read_bytes().decode('utf-8'))
        m['contentLocation'] = location
        tamper(self.d / 'artifact.json', json.dumps(m).encode('utf-8'))
        with self.assertRaises(ArtifactIntegrityError) as cm:
            self.store.read(self.a.artifact_id)
        self.assertTrue({p.code for p in cm.exception.problems} & expected_codes, location)

    def test_path_traversal(self):
        self.check(f'artifacts/{self.a.artifact_id}/../../store.json', {'BAD_LOCATION'})

    def test_outside_artifact_directory(self):
        self.check('../outside/payload.bin', {'BAD_LOCATION'})

    def test_absolute_paths(self):
        self.check('/etc/passwd', {'BAD_LOCATION'})
        self.check('C:/Windows/win.ini', {'BAD_LOCATION', 'INVALID_METADATA'})

    def test_other_artifacts_payload_even_with_matching_hash(self):
        m = json.loads((self.d / 'artifact.json').read_bytes().decode('utf-8'))
        m['contentLocation'] = f'artifacts/{self.b.artifact_id}/payload.bin'
        m['contentHash'], m['byteSize'] = self.b.content_hash, len(self.b.content)
        tamper(self.d / 'artifact.json', json.dumps(m).encode('utf-8'))
        with self.assertRaises(ArtifactIntegrityError) as cm:
            self.store.read(self.a.artifact_id)
        self.assertIn('BAD_LOCATION', {p.code for p in cm.exception.problems})

    def test_inline_content_is_not_accepted_in_the_store(self):
        m = json.loads((self.d / 'artifact.json').read_bytes().decode('utf-8'))
        m['content'] = 'answer A'
        tamper(self.d / 'artifact.json', json.dumps(m).encode('utf-8'))
        with self.assertRaises(ArtifactIntegrityError):
            self.store.read(self.a.artifact_id)

    def test_nonexistent_payload_path(self):
        self.check(f'artifacts/{self.a.artifact_id}/missing.bin', {'BAD_LOCATION'})
