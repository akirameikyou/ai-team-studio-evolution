"""Append-only E001 event log: one write-once file per event under <store>/events/.

The file name is the zero-padded sequence number, so two events can never share a
sequence: the second exclusive create fails. Events are never rewritten or deleted.
This is the minimal Artifact <-> Event link of E001-C, not a tamper-evident chain:
an event file edited outside the application is detected only where another record
cross-checks it (see docs/STORAGE_E001-C.md).
"""
import datetime
import json
import re
import uuid
from pathlib import Path
from typing import NamedTuple

from domain.schema import validator
from infrastructure.storage.integrity import create_exclusive

SCHEMA_VERSION = 'E001/1.0'

# Reserved E001 system actor (docs/STORAGE_E001-C.md, TRACEABILITY D-18).
# It records persistence operations performed by the Studio itself (ArtifactStore / EventLog).
# It is not a human, not an AI and not a provider. E001-D must register it as the SYSTEM node.
SYSTEM_ACTOR_ID = 'NODE-studio'

_EVENT_FILE = re.compile(r'^(\d{10})\.json$')


class EventSequenceCollision(Exception):
    """An event with this sequence number already exists; it was left untouched."""


class InvalidEvent(Exception):
    def __init__(self, issues):
        super().__init__('; '.join(f'{i.path}: {i.message}' for i in issues))
        self.issues = issues


class Problem(NamedTuple):
    code: str
    subject: str
    message: str


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def render(record) -> bytes:
    return (json.dumps(record, ensure_ascii=False, indent=2) + '\n').encode('utf-8')


class EventLog:
    def __init__(self, store_root):
        self.dir = Path(store_root) / 'events'
        self.dir.mkdir(parents=True, exist_ok=True)

    def _sequences(self):
        return sorted(int(m.group(1)) for p in self.dir.iterdir() if (m := _EVENT_FILE.match(p.name)))

    def _next_sequence(self):
        seqs = self._sequences()
        return (seqs[-1] if seqs else 0) + 1

    def append(self, *, event_type, target_type, target_id, payload, artifact_refs=(), actor_node_id=SYSTEM_ACTOR_ID):
        seq = self._next_sequence()
        event = {
            'schemaVersion': SCHEMA_VERSION, 'eventId': f'EVT-{uuid.uuid4().hex}', 'sequence': seq,
            'timestamp': utc_now(), 'actorNodeId': actor_node_id, 'eventType': event_type,
            'targetType': target_type, 'targetId': target_id, 'payload': dict(payload),
            'artifactRefs': list(artifact_refs),
        }
        issues = validator.validate_record('event', event)
        if issues:
            raise InvalidEvent(issues)
        try:
            create_exclusive(self.dir / f'{seq:010d}.json', render(event))
        except FileExistsError:
            raise EventSequenceCollision(f'event sequence {seq} already exists') from None
        return event

    def read_all(self):
        """Return (events in sequence order, problems). Unreadable files are reported, never repaired."""
        events, problems = [], []
        for p in sorted(self.dir.iterdir(), key=lambda x: x.name):
            m = _EVENT_FILE.match(p.name)
            if not m:
                problems.append(Problem('UNEXPECTED_FILE', f'events/{p.name}', 'not an event file'))
                continue
            try:
                ev = json.loads(p.read_bytes().decode('utf-8'))
            except (UnicodeDecodeError, json.JSONDecodeError) as e:
                problems.append(Problem('BAD_EVENT_FILE', f'events/{p.name}', f'unreadable: {e}'))
                continue
            issues = validator.validate_record('event', ev) if isinstance(ev, dict) else ['not an object']
            if issues:
                problems.append(Problem('BAD_EVENT_FILE', f'events/{p.name}', f'invalid event: {issues[0]}'))
                continue
            if ev['sequence'] != int(m.group(1)):
                problems.append(Problem('SEQUENCE_MISMATCH', f'events/{p.name}', f'file holds sequence {ev["sequence"]}'))
                continue
            events.append(ev)
        for expected, seq in enumerate(self._sequences(), 1):
            if seq != expected:
                problems.append(Problem('SEQUENCE_GAP', 'events', f'expected sequence {expected}, found {seq}'))
                break
        return events, problems

    def artifact_events(self):
        """artifactId -> ARTIFACT_RECEIVED events naming it as target."""
        events, _ = self.read_all()
        out = {}
        for ev in events:
            if ev['eventType'] == 'ARTIFACT_RECEIVED' and ev['targetType'] == 'artifact':
                out.setdefault(ev['targetId'], []).append(ev)
        return out

    def verify(self):
        return self.read_all()[1]
