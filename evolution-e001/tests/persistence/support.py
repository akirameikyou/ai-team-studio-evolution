"""Shared helpers for E001-C persistence tests. Every test works in its own temporary directory."""
import os
import stat
import tempfile
import unittest
from pathlib import Path

from infrastructure.storage.artifact_store import ArtifactStore

CASE = 'CASE-T-001'


def tamper(path, data: bytes):
    """Simulate an outside program editing a stored file (it clears the read-only attribute first)."""
    path = Path(path)
    if path.exists():
        os.chmod(path, stat.S_IREAD | stat.S_IWRITE)
    path.write_bytes(data)


def remove(path):
    path = Path(path)
    os.chmod(path, stat.S_IREAD | stat.S_IWRITE)
    path.unlink()


def snapshot(root):
    """Every file under root with its bytes, to prove that nothing changed."""
    root = Path(root)
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob('*')) if p.is_file()}


class StoreTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.store = ArtifactStore(self.tmp / 'store')

    def tearDown(self):
        self._tmp.cleanup()

    def new_store(self, name):
        return ArtifactStore(self.tmp / name)

    def put(self, content=b'RAW response\n', store=None, **kw):
        args = dict(kind='RAW_RESPONSE', case_id=CASE, source_kind='AI', received_via='MANUAL_PASTE')
        args.update(kw)
        return (store or self.store).create(content=content, **args)

    def adir(self, artifact_id, store=None):
        return (store or self.store).artifacts_dir / artifact_id

    def codes(self, problems):
        return {p.code for p in problems}
