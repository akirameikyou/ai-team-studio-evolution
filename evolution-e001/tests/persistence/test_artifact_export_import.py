"""E001-C: export / import keeps byte identity; nothing is merged or overwritten."""
import hashlib
import json
from unittest import mock

from infrastructure.storage.artifact_store import ArtifactIntegrityError, ExportRejected, ImportRejected
from tests.persistence.support import StoreTestCase, snapshot, tamper


class ExportImportTests(StoreTestCase):
    def setUp(self):
        super().setUp()
        self.a = self.put('回答 A\r\nSECRET-A\n'.encode('utf-8'), artifact_id='ART-exp-a')
        self.b = self.put(b'answer B', artifact_id='ART-exp-b', run_id='RUN-T-001')
        self.pkg = self.tmp / 'package'
        self.store.export_artifacts(self.pkg)

    def assertRejected(self, target, code):
        before = snapshot(target.root)
        with self.assertRaises(ImportRejected) as cm:
            target.import_artifacts(self.pkg)
        self.assertIn(code, {p.code for p in cm.exception.problems})
        self.assertEqual(snapshot(target.root), before)  # all-or-nothing: nothing written

    def test_round_trip_preserves_bytes_and_hashes(self):
        other = self.new_store('other')
        result = other.import_artifacts(self.pkg)
        self.assertEqual(sorted(result.created), ['ART-exp-a', 'ART-exp-b'])
        for src in (self.a, self.b):
            got = other.read(src.artifact_id)
            self.assertEqual(got.content, src.content)
            self.assertEqual(got.metadata_bytes, src.metadata_bytes)  # metadata bytes copied, not re-serialized
            self.assertEqual((got.content_hash, got.metadata_hash), (src.content_hash, src.metadata_hash))
            self.assertTrue(got.event_linked)
        self.assertEqual(other.verify(), [])
        vias = [e['payload']['via'] for e in other.events.read_all()[0]]
        self.assertEqual(vias, ['IMPORT', 'IMPORT'])

    def test_reimport_of_identical_artifacts_is_skipped(self):
        other = self.new_store('other')
        other.import_artifacts(self.pkg)
        before = snapshot(other.root)
        result = other.import_artifacts(self.pkg)
        self.assertEqual((result.created, sorted(result.skipped)), ([], ['ART-exp-a', 'ART-exp-b']))
        self.assertEqual(snapshot(other.root), before)

    def test_same_id_different_content_is_a_collision(self):
        other = self.new_store('other')
        self.put(b'different content', artifact_id='ART-exp-a', store=other)
        self.assertRejected(other, 'COLLISION')

    def test_same_id_same_content_different_metadata_is_a_collision(self):
        other = self.new_store('other')
        self.put(self.a.content, artifact_id='ART-exp-a', store=other, case_id='CASE-other-1')
        self.assertRejected(other, 'COLLISION')

    def test_tampered_payload_rejects_the_whole_package(self):
        tamper(self.pkg / 'artifacts' / 'ART-exp-b' / 'payload.bin', b'answer b')
        self.assertRejected(self.new_store('other'), 'HASH_MISMATCH')

    def test_tampered_metadata_rejects_the_whole_package(self):
        p = self.pkg / 'artifacts' / 'ART-exp-a' / 'artifact.json'
        m = json.loads(p.read_bytes())
        m['provenance']['caseId'] = 'CASE-other-1'
        tamper(p, json.dumps(m).encode())
        self.assertRejected(self.new_store('other'), 'METADATA_MISMATCH')

    def test_bad_location_in_package_is_rejected(self):
        p = self.pkg / 'artifacts' / 'ART-exp-a' / 'artifact.json'
        m = json.loads(p.read_bytes())
        m['contentLocation'] = 'artifacts/ART-exp-b/payload.bin'
        data = json.dumps(m).encode()
        tamper(p, data)
        manifest = json.loads((self.pkg / 'export.json').read_bytes())
        manifest['artifacts'][0]['metadataHash'] = hashlib.sha256(data).hexdigest()  # manifest re-forged too
        tamper(self.pkg / 'export.json', json.dumps(manifest).encode())
        self.assertRejected(self.new_store('other'), 'BAD_LOCATION')

    def test_unlisted_artifact_directory_is_rejected(self):
        extra = self.pkg / 'artifacts' / 'ART-exp-c'
        extra.mkdir()
        self.assertRejected(self.new_store('other'), 'UNLISTED')

    def test_unsupported_export_version_is_rejected(self):
        manifest = json.loads((self.pkg / 'export.json').read_bytes())
        manifest['schemaVersion'] = 'E002/1.0'
        tamper(self.pkg / 'export.json', json.dumps(manifest).encode())
        self.assertRejected(self.new_store('other'), 'UNSUPPORTED_EXPORT')

    def test_export_refuses_corrupt_orphan_or_nonempty_destination(self):
        with self.assertRaises(ExportRejected):
            self.store.export_artifacts(self.pkg)  # not empty
        with mock.patch.object(self.store.events, 'append', side_effect=OSError('crash')):
            with self.assertRaises(OSError):
                self.put(b'orphan', artifact_id='ART-exp-orphan')
        with self.assertRaises(ExportRejected):
            self.store.export_artifacts(self.tmp / 'pkg2', ['ART-exp-orphan'])
        tamper(self.adir('ART-exp-a') / 'payload.bin', b'changed')
        with self.assertRaises(ArtifactIntegrityError):
            self.store.export_artifacts(self.tmp / 'pkg3', ['ART-exp-a'])
