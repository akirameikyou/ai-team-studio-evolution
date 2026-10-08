"""Byte-level integrity helpers for the E001 file store (standard library only)."""
import hashlib
import os
import stat
import uuid
from pathlib import Path


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def create_exclusive(path: Path, data: bytes, read_only: bool = True) -> None:
    """Create a new file holding exactly `data`. Never replaces an existing file.

    The bytes are written and fsynced to a temporary file in the same directory, then
    hard-linked to `path`. os.link fails with FileExistsError when `path` exists, so an
    existing file is never touched, and `path` only ever appears with complete content.
    A crash can leave a hidden `.<name>.<random>.tmp` file behind; store verification
    reports it instead of deleting it.

    read_only sets the OS read-only attribute. This guards against accidental writes by
    other programs; it is not tamper resistance (anyone can clear the attribute).
    """
    path = Path(path)
    tmp = path.with_name(f'.{path.name}.{uuid.uuid4().hex}.tmp')
    with open(tmp, 'xb') as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    try:
        os.link(tmp, path)
    finally:
        os.remove(tmp)
    if read_only:
        os.chmod(path, stat.S_IREAD)
