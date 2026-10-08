"""Write-once persistence for schema records (E001-D).

This is a storage mechanism only. It knows how to store, read back and check a record of a
given schema entity type; what a Case, Run or Manifest means lives in application/.

Layout under the store root (next to artifacts/ and events/ of E001-C):

    records/<entity>/<id>.json                 unversioned entities
    records/<entity>/<id>/<version>.json       pattern, route, protocol

A record file is created exclusively and never rewritten. Artifacts and events are not
stored here: they belong to ArtifactStore and EventLog.
"""
import json
import re
from pathlib import Path
from typing import NamedTuple

from domain.schema import validator
from infrastructure.storage.event_log import Problem, render
from infrastructure.storage.integrity import create_exclusive, sha256_hex

_SCHEMA = validator.load_schema()
ENTITIES = {k: v for k, v in _SCHEMA['x-entities'].items() if k not in ('artifact', 'event')}
# Checked before any id or version is used in a path, so a lookup can never leave records/.
_ID = re.compile(r'^[A-Z]+-[A-Za-z0-9][A-Za-z0-9_-]{2,79}$')
_VERSION = re.compile(r'^[0-9]+\.[0-9]+$')


class RecordStoreError(Exception):
    pass


class UnknownEntity(RecordStoreError):
    pass


class DuplicateRecord(RecordStoreError):
    pass


class RecordNotFound(RecordStoreError):
    pass


class InvalidRecord(RecordStoreError):
    def __init__(self, entity, issues):
        super().__init__(f'{entity}: ' + '; '.join(f'{i.path}: {i.message}' for i in issues))
        self.issues = issues


class RecordIntegrityError(RecordStoreError):
    def __init__(self, path, problems):
        super().__init__(f'{path}: ' + '; '.join(f'{p.code} {p.message}' for p in problems))
        self.problems = problems


class StoredRecord(NamedTuple):
    entity: str
    record_bytes: bytes
    record_hash: str

    def record(self):
        """A fresh copy each call; changing it never changes the store."""
        return json.loads(self.record_bytes.decode('utf-8'))


class RecordStore:
    def __init__(self, store_root):
        self.root = Path(store_root)
        if not (self.root / 'store.json').is_file():
            raise RecordStoreError(f'{self.root} is not an initialized E001 store (open it with ArtifactStore first)')
        self.dir = self.root / 'records'
        self.dir.mkdir(exist_ok=True)

    def _meta(self, entity):
        if entity not in ENTITIES:
            raise UnknownEntity(f'{entity!r} is not stored in RecordStore')
        return ENTITIES[entity]

    def _path(self, entity, record_id, version=None):
        meta = self._meta(entity)
        if not isinstance(record_id, str) or not _ID.match(record_id):
            raise RecordNotFound(f'not a record id: {record_id!r}')
        if meta['versioned']:
            if not isinstance(version, str) or not _VERSION.match(version):
                raise RecordNotFound(f'{entity} is versioned; not a version: {version!r}')
            return self.dir / entity / record_id / f'{version}.json'
        return self.dir / entity / f'{record_id}.json'

    def put(self, entity, record):
        """Validate and store a new record. Returns the StoredRecord. Never overwrites."""
        meta = self._meta(entity)
        issues = validator.validate_record(entity, record)
        if issues:
            raise InvalidRecord(entity, issues)  # nothing written
        record_id, version = record[meta['idField']], record.get('version') if meta['versioned'] else None
        path = self._path(entity, record_id, version)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            create_exclusive(path, render(record))
        except FileExistsError:
            key = record_id + (f' v{version}' if version else '')
            raise DuplicateRecord(f'{entity} {key} already exists; records are write-once') from None
        return self.get(entity, record_id, version)

    def exists(self, entity, record_id, version=None):
        try:
            return self._path(entity, record_id, version).is_file()
        except RecordNotFound:
            return False

    def get(self, entity, record_id, version=None):
        """Read and check one record: parses, matches the schema, and names the id/version of its path."""
        meta = self._meta(entity)
        path = self._path(entity, record_id, version)
        if not path.is_file():
            raise RecordNotFound(f'{entity} {record_id}' + (f' v{version}' if version else ''))
        data = path.read_bytes()
        problems = self._check(entity, meta, data, record_id, version)
        if problems:
            raise RecordIntegrityError(self._rel(path), problems)
        return StoredRecord(entity, data, sha256_hex(data))

    def list(self, entity):
        """Keys present on disk: ids, or (id, version) pairs for versioned entities."""
        meta = self._meta(entity)
        base = self.dir / entity
        if not base.is_dir():
            return []
        if meta['versioned']:
            return sorted((d.name, f.stem) for d in base.iterdir() if d.is_dir() for f in d.glob('*.json'))
        return sorted(f.stem for f in base.glob('*.json'))

    def records(self, entity):
        """All records of one entity as fresh dicts (each read is checked)."""
        versioned = self._meta(entity)['versioned']
        return [(self.get(entity, *k) if versioned else self.get(entity, k)).record() for k in self.list(entity)]

    def verify(self):
        """Check every record file. Unknown files and leftover temporaries are reported, never removed."""
        problems = []
        for entity_dir in sorted(self.dir.iterdir()):
            if entity_dir.name not in ENTITIES or not entity_dir.is_dir():
                problems.append(Problem('UNEXPECTED_FILE', self._rel(entity_dir), 'not a record entity directory'))
                continue
            meta = ENTITIES[entity_dir.name]
            for item in sorted(entity_dir.iterdir()):
                if not meta['versioned']:
                    problems += self._check_file(entity_dir.name, meta, item, item.stem, None)
                elif not item.is_dir():
                    problems.append(Problem('UNEXPECTED_FILE', self._rel(item), 'not a record directory'))
                else:
                    for f in sorted(item.iterdir()):
                        problems += self._check_file(entity_dir.name, meta, f, item.name, f.stem)
        return problems

    def _check_file(self, entity, meta, f, record_id, version):
        if not f.is_file() or f.suffix != '.json' or f.name.startswith('.'):
            return [Problem('UNEXPECTED_FILE', self._rel(f), 'not a record file')]
        return self._check(entity, meta, f.read_bytes(), record_id, version)

    def _check(self, entity, meta, data, record_id, version):
        rel = f'records/{entity}/{record_id}' + (f'/{version}' if version else '')
        try:
            rec = json.loads(data.decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            return [Problem('INVALID_RECORD', rel, f'unreadable: {e}')]
        issues = validator.validate_record(entity, rec) if isinstance(rec, dict) else ['not an object']
        if issues:
            return [Problem('INVALID_RECORD', rel, f'fails the schema: {issues[0]}')]
        out = []
        if rec[meta['idField']] != record_id:
            out.append(Problem('ID_MISMATCH', rel, f'file holds {rec[meta["idField"]]}'))
        if meta['versioned'] and rec.get('version') != version:
            out.append(Problem('VERSION_MISMATCH', rel, f'file holds version {rec.get("version")}'))
        return out

    def _rel(self, path):
        return path.relative_to(self.root).as_posix()
