"""E001 write-once Artifact store (RAW evidence layer).

Stores what the Studio received, byte for byte. Layout under the store root:

    store.json                              format marker, written once
    artifacts/<artifactId>/payload.bin      exact received bytes
    artifacts/<artifactId>/artifact.json    schema 'artifact' record (contentLocation form)
    events/<sequence>.json                  see event_log.py

Guarantees are application-level only: this module offers no update or delete path,
creates every file exclusively, and verifies SHA-256 on every read. It cannot stop
another program from editing the files; it detects such edits (docs/STORAGE_E001-C.md).
"""
import dataclasses
import json
import re
import uuid
from pathlib import Path
from typing import NamedTuple

from domain.schema import validator
from infrastructure.storage.event_log import SCHEMA_VERSION, SYSTEM_ACTOR_ID, EventLog, Problem, render, utc_now
from infrastructure.storage.integrity import create_exclusive, sha256_hex

STORE_FORMAT = 'E001_ARTIFACT_STORE'
EXPORT_FORMAT = 'E001_ARTIFACT_EXPORT'
LAYOUT_VERSION = 1
PAYLOAD, METADATA = 'payload.bin', 'artifact.json'
_ARTIFACT_ID = re.compile(r'^ART-[A-Za-z0-9][A-Za-z0-9_-]{2,79}$')


class StoreError(Exception):
    pass


class UnsupportedStore(StoreError):
    pass


class DuplicateArtifactId(StoreError):
    pass


class ArtifactNotFound(StoreError):
    pass


class UnsupportedContent(StoreError):
    pass


class InvalidArtifact(StoreError):
    def __init__(self, issues):
        super().__init__('; '.join(f'{i.path}: {i.message}' for i in issues))
        self.issues = issues


class ArtifactIntegrityError(StoreError):
    def __init__(self, artifact_id, problems):
        super().__init__(f'{artifact_id}: ' + '; '.join(f'{p.code} {p.message}' for p in problems))
        self.artifact_id = artifact_id
        self.problems = problems


class ExportRejected(StoreError):
    pass


class ImportRejected(StoreError):
    def __init__(self, problems):
        super().__init__('; '.join(f'{p.code} {p.subject}: {p.message}' for p in problems))
        self.problems = problems


@dataclasses.dataclass(frozen=True)
class StoredArtifact:
    """A verified read. Immutable: bytes fields, and metadata() returns a fresh copy."""
    artifact_id: str
    content: bytes
    metadata_bytes: bytes
    content_hash: str
    metadata_hash: str
    event_linked: bool  # False = ORPHAN: no ARTIFACT_RECEIVED event records this artifact

    def metadata(self):
        return json.loads(self.metadata_bytes.decode('utf-8'))

    def text(self):
        return self.content.decode('utf-8')


class ArtifactCheck(NamedTuple):
    status: str  # OK | ORPHAN | INCOMPLETE | CORRUPT
    problems: list


class ImportResult(NamedTuple):
    created: list
    skipped: list


def expected_location(artifact_id):
    return f'artifacts/{artifact_id}/{PAYLOAD}'


class ArtifactStore:
    """Write-once artifact storage. Public operations: create, read, inspect, list_ids, verify,
    export_artifacts, import_artifacts. There is deliberately no update, replace or delete."""

    def __init__(self, root):
        self.root = Path(root)
        self._open_or_initialize()
        self.events = EventLog(self.root)
        self.artifacts_dir = self.root / 'artifacts'
        self.artifacts_dir.mkdir(exist_ok=True)

    def _open_or_initialize(self):
        marker = self.root / 'store.json'
        if marker.exists():
            try:
                info = json.loads(marker.read_bytes().decode('utf-8'))
            except (UnicodeDecodeError, json.JSONDecodeError) as e:
                raise UnsupportedStore(f'unreadable store.json: {e}') from None
            if info.get('format') != STORE_FORMAT or info.get('schemaVersion') != SCHEMA_VERSION or info.get('layoutVersion') != LAYOUT_VERSION:
                raise UnsupportedStore(f'unsupported store: {info}')
            return
        if self.root.exists() and any(self.root.iterdir()):
            raise UnsupportedStore(f'{self.root} is not empty and has no store.json; refusing to adopt it')
        self.root.mkdir(parents=True, exist_ok=True)
        create_exclusive(marker, render({'format': STORE_FORMAT, 'schemaVersion': SCHEMA_VERSION,
                                         'layoutVersion': LAYOUT_VERSION, 'createdAt': utc_now()}))

    # ---------- create ----------

    def create(self, *, content, kind, case_id, source_kind, received_via, run_id=None, transfer_id=None,
               producer_node_id=None, parent_artifact_ids=(), artifact_id=None):
        """Store `content` (bytes, exactly as received) as a new artifact and record ARTIFACT_RECEIVED."""
        if not isinstance(content, bytes):
            raise TypeError('content must be bytes; encode text exactly once at the input boundary')
        try:
            content.decode('utf-8')
        except UnicodeDecodeError:
            raise UnsupportedContent('E001-C stores UTF-8 payloads only (implementation constraint)') from None
        artifact_id = artifact_id or f'ART-{uuid.uuid4().hex}'
        meta = {
            'schemaVersion': SCHEMA_VERSION, 'artifactId': artifact_id, 'kind': kind,
            'contentLocation': expected_location(artifact_id), 'encoding': 'utf-8',
            'byteSize': len(content), 'contentHash': sha256_hex(content), 'hashAlgorithm': 'SHA-256',
            'createdAt': utc_now(),
            'provenance': {'sourceKind': source_kind, 'receivedVia': received_via, 'caseId': case_id,
                           'runId': run_id, 'transferId': transfer_id},
            'producerNodeId': producer_node_id, 'parentArtifactIds': list(parent_artifact_ids),
        }
        issues = validator.validate_record('artifact', meta)
        if issues:
            raise InvalidArtifact(issues)  # nothing has been written yet
        meta_bytes = render(meta)
        self._write(artifact_id, content, meta_bytes)
        return self._record_event(artifact_id, meta, meta_bytes, via=received_via)

    def _reserve(self, artifact_id):
        # Exclusive directory creation reserves the ID; a reserved ID is never reused.
        try:
            (self.artifacts_dir / artifact_id).mkdir()
        except FileExistsError:
            raise DuplicateArtifactId(f'{artifact_id} already exists; artifacts are write-once') from None

    def _write(self, artifact_id, content, meta_bytes):
        self._reserve(artifact_id)
        d = self.artifacts_dir / artifact_id
        create_exclusive(d / PAYLOAD, content)
        create_exclusive(d / METADATA, meta_bytes)  # commit point
        check = self.inspect(artifact_id, require_event=False)
        if check.status != 'OK':
            raise ArtifactIntegrityError(artifact_id, check.problems)

    def _record_event(self, artifact_id, meta, meta_bytes, via):
        self.events.append(
            event_type='ARTIFACT_RECEIVED', target_type='artifact', target_id=artifact_id,
            payload={'artifactHash': meta['contentHash'], 'metadataHash': sha256_hex(meta_bytes),
                     'byteSize': meta['byteSize'], 'kind': meta['kind'], 'caseId': meta['provenance']['caseId'],
                     'runId': meta['provenance']['runId'], 'via': via},
            artifact_refs=[artifact_id], actor_node_id=SYSTEM_ACTOR_ID)
        return self.read(artifact_id)

    # ---------- read / verify ----------

    def list_ids(self):
        return sorted(p.name for p in self.artifacts_dir.iterdir() if p.is_dir())

    def _load(self, artifact_id, event_index=None, require_event=True):
        """Check one artifact without trusting any of its files. Returns (problems, content, meta_bytes)."""
        if not _ARTIFACT_ID.match(str(artifact_id)):
            raise ArtifactNotFound(f'not an artifact id: {artifact_id!r}')
        d = self.artifacts_dir / artifact_id
        if not d.is_dir():
            raise ArtifactNotFound(artifact_id)
        problems = []

        def bad(code, message):
            problems.append(Problem(code, artifact_id, message))

        for p in d.iterdir():
            if p.name not in (PAYLOAD, METADATA):
                bad('UNEXPECTED_FILE', f'unexpected file {p.name}')
        if not (d / METADATA).is_file():
            bad('INCOMPLETE', 'artifact.json is missing (creation did not reach its commit point)')
            return problems, None, None
        meta_bytes = (d / METADATA).read_bytes()
        try:
            meta = json.loads(meta_bytes.decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            bad('INVALID_METADATA', f'unreadable artifact.json: {e}')
            return problems, None, meta_bytes
        issues = validator.validate_record('artifact', meta) if isinstance(meta, dict) else ['not an object']
        if issues:
            bad('INVALID_METADATA', f'artifact.json fails the schema: {issues[0]}')
            return problems, None, meta_bytes
        if meta['artifactId'] != artifact_id:
            bad('ID_MISMATCH', f'artifact.json names {meta["artifactId"]}')
        # The payload location is never followed from metadata: it must equal the one fixed path.
        if 'content' in meta or meta.get('contentLocation') != expected_location(artifact_id):
            bad('BAD_LOCATION', f'contentLocation {meta.get("contentLocation")!r} is not {expected_location(artifact_id)!r}')
            return problems, None, meta_bytes
        payload_path = d / PAYLOAD
        if not payload_path.is_file():
            bad('MISSING_PAYLOAD', 'payload.bin is missing')
            return problems, None, meta_bytes
        content = payload_path.read_bytes()
        if len(content) != meta['byteSize']:
            bad('SIZE_MISMATCH', f'{len(content)} bytes stored, metadata says {meta["byteSize"]}')
        if sha256_hex(content) != meta['contentHash']:
            bad('HASH_MISMATCH', 'SHA-256 of payload.bin differs from contentHash')
        if require_event:
            index = self.events.artifact_events() if event_index is None else event_index
            linked = index.get(artifact_id, [])
            if not linked:
                bad('ORPHAN', 'no ARTIFACT_RECEIVED event records this artifact')
            elif len(linked) > 1:
                bad('DUPLICATE_EVENT', f'{len(linked)} ARTIFACT_RECEIVED events name this artifact')
            else:
                ev = linked[0]['payload']
                if ev.get('artifactHash') != meta['contentHash']:
                    bad('EVENT_HASH_MISMATCH', 'contentHash differs from the hash recorded in its event')
                if ev.get('metadataHash') != sha256_hex(meta_bytes):
                    bad('METADATA_MISMATCH', 'artifact.json differs from the state recorded in its event')
        return problems, content, meta_bytes

    def inspect(self, artifact_id, require_event=True):
        problems, _, _ = self._load(artifact_id, require_event=require_event)
        codes = {p.code for p in problems}
        if not codes:
            return ArtifactCheck('OK', problems)
        if codes == {'ORPHAN'}:
            return ArtifactCheck('ORPHAN', problems)
        if 'INCOMPLETE' in codes:
            return ArtifactCheck('INCOMPLETE', problems)
        return ArtifactCheck('CORRUPT', problems)

    def read(self, artifact_id):
        """Return verified bytes. Raises ArtifactIntegrityError on any problem except ORPHAN,
        which is returned with event_linked=False so the caller sees it."""
        problems, content, meta_bytes = self._load(artifact_id)
        blocking = [p for p in problems if p.code != 'ORPHAN']
        if blocking:
            raise ArtifactIntegrityError(artifact_id, blocking)
        meta = json.loads(meta_bytes.decode('utf-8'))
        return StoredArtifact(artifact_id, content, meta_bytes, meta['contentHash'], sha256_hex(meta_bytes),
                              event_linked=not problems)

    def verify(self):
        """Check the whole store. Returns a list of Problems; an empty list means fully consistent."""
        problems = list(self.events.verify())
        index = self.events.artifact_events()
        for p in self.artifacts_dir.iterdir():
            if not p.is_dir():
                problems.append(Problem('UNEXPECTED_FILE', f'artifacts/{p.name}', 'not an artifact directory'))
            elif not _ARTIFACT_ID.match(p.name):
                problems.append(Problem('UNEXPECTED_FILE', f'artifacts/{p.name}', 'not an artifact id'))
            else:
                problems += self._load(p.name, event_index=index)[0]
        for aid in index:
            if not (self.artifacts_dir / aid).is_dir():
                problems.append(Problem('EVENT_WITHOUT_ARTIFACT', aid, 'event names an artifact that does not exist'))
        return problems

    # ---------- export / import ----------

    def export_artifacts(self, dest, artifact_ids=None):
        """Copy artifacts byte for byte into a new directory with a hash manifest.
        Only fully verified, event-linked artifacts are exported."""
        dest = Path(dest)
        if dest.exists() and any(dest.iterdir()):
            raise ExportRejected(f'{dest} is not empty')
        ids = list(artifact_ids) if artifact_ids is not None else self.list_ids()
        verified = []
        for aid in ids:
            art = self.read(aid)
            if not art.event_linked:
                raise ExportRejected(f'{aid} is ORPHAN (no ARTIFACT_RECEIVED event); not exported')
            verified.append(art)
        for art in verified:
            d = dest / 'artifacts' / art.artifact_id
            d.mkdir(parents=True)
            create_exclusive(d / PAYLOAD, art.content, read_only=False)
            create_exclusive(d / METADATA, art.metadata_bytes, read_only=False)
        manifest = {'format': EXPORT_FORMAT, 'schemaVersion': SCHEMA_VERSION, 'layoutVersion': LAYOUT_VERSION,
                    'createdAt': utc_now(),
                    'artifacts': [{'artifactId': a.artifact_id, 'contentHash': a.content_hash,
                                   'metadataHash': a.metadata_hash, 'byteSize': len(a.content)} for a in verified]}
        create_exclusive(dest / 'export.json', render(manifest), read_only=False)
        return dest / 'export.json'

    def import_artifacts(self, src):
        """All-or-nothing import. Every file is verified before anything is written. An existing id is
        skipped only if artifactId, contentHash and metadataHash all match; otherwise the import is
        rejected. Nothing is ever merged or overwritten."""
        src = Path(src)
        problems, plan = [], []

        def bad(code, subject, message):
            problems.append(Problem(code, subject, message))

        try:
            manifest = json.loads((src / 'export.json').read_bytes().decode('utf-8'))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
            raise ImportRejected([Problem('BAD_MANIFEST', 'export.json', str(e))]) from None
        if (manifest.get('format'), manifest.get('schemaVersion'), manifest.get('layoutVersion')) != (EXPORT_FORMAT, SCHEMA_VERSION, LAYOUT_VERSION):
            raise ImportRejected([Problem('UNSUPPORTED_EXPORT', 'export.json', 'unsupported format or version')])
        entries = manifest.get('artifacts')
        if not isinstance(entries, list):
            raise ImportRejected([Problem('BAD_MANIFEST', 'export.json', 'artifacts list missing')])
        listed = set()
        for e in entries:
            aid = e.get('artifactId') if isinstance(e, dict) else None
            if not isinstance(aid, str) or not _ARTIFACT_ID.match(aid) or aid in listed:
                bad('BAD_MANIFEST', str(aid), 'invalid or repeated artifactId')
                continue
            listed.add(aid)
            d = src / 'artifacts' / aid
            try:
                content, meta_bytes = (d / PAYLOAD).read_bytes(), (d / METADATA).read_bytes()
                meta = json.loads(meta_bytes.decode('utf-8'))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as ex:
                bad('UNREADABLE', aid, str(ex))
                continue
            if not isinstance(meta, dict) or validator.validate_record('artifact', meta):
                bad('INVALID_METADATA', aid, 'artifact.json fails the schema')
                continue
            if meta['artifactId'] != aid:
                bad('ID_MISMATCH', aid, f'artifact.json names {meta["artifactId"]}')
            if 'content' in meta or meta.get('contentLocation') != expected_location(aid):
                bad('BAD_LOCATION', aid, f'contentLocation {meta.get("contentLocation")!r}')
            try:
                content.decode('utf-8')
            except UnicodeDecodeError:
                bad('UNSUPPORTED_CONTENT', aid, 'payload is not UTF-8')
            got = (sha256_hex(content), sha256_hex(meta_bytes), len(content))
            if got[0] != e.get('contentHash') or got[0] != meta['contentHash']:
                bad('HASH_MISMATCH', aid, 'payload SHA-256 differs from manifest/metadata')
            if got[1] != e.get('metadataHash'):
                bad('METADATA_MISMATCH', aid, 'artifact.json SHA-256 differs from manifest')
            if got[2] != e.get('byteSize') or got[2] != meta['byteSize']:
                bad('SIZE_MISMATCH', aid, 'byte size differs from manifest/metadata')
            plan.append((aid, content, meta_bytes, meta))
        package_dirs = {p.name for p in (src / 'artifacts').iterdir()} if (src / 'artifacts').is_dir() else set()
        for extra in sorted(package_dirs - listed):
            bad('UNLISTED', extra, 'artifact directory not listed in export.json')
        created, skipped = [], []
        for aid, content, meta_bytes, meta in plan:
            if (self.artifacts_dir / aid).exists():
                try:
                    have = self.read(aid)
                except StoreError as ex:
                    bad('COLLISION', aid, f'existing artifact cannot be verified: {ex}')
                    continue
                if (have.artifact_id, have.content_hash, have.metadata_hash) == (aid, sha256_hex(content), sha256_hex(meta_bytes)):
                    skipped.append(aid)
                else:
                    bad('COLLISION', aid, 'an artifact with this id already exists with different content or metadata')
        if problems:
            raise ImportRejected(problems)  # nothing has been written
        for aid, content, meta_bytes, meta in plan:
            if aid in skipped:
                continue
            self._write(aid, content, meta_bytes)  # the exact package bytes, never re-serialized
            self._record_event(aid, meta, meta_bytes, via='IMPORT')
            created.append(aid)
        return ImportResult(created, skipped)

    def bundle_records(self):
        """Artifact records and their events in schema form, for validate_bundle (E001-B)."""
        events, _ = self.events.read_all()
        return {'artifact': [self.read(a).metadata() for a in self.list_ids()], 'event': events}
