"""Deterministic packet format AITS-PACKET/1 and canonical artifact order (E001-F1/U2).

Pure functions: no I/O, no clock, no randomness. U2 only converts between packet bytes and
structured values and checks the format. It does not choose or drop artifacts, and it does not
check that the run, edge, nodes or protocol named in a packet agree with any Manifest, Route,
Transfer or provenance record; that is the job of later units (U3/U4).

Layout (frame lines are ASCII, each ends with a single LF; artifact bodies are copied byte for byte):

    AITS-PACKET/1
    run: <runId>
    edge: <edgeId>
    from: <nodeId>
    to: <nodeId>
    protocol: <protocolId>@<version>
    artifacts: <N>
    --- ARTIFACT <i>/<N> id=<artifactId> kind=<kind> sha256=<hex64> bytes=<n>
    <n bytes of body>
    --- END ARTIFACT <i>/<N>
    ... (N times)
    --- END PACKET

The packet hash is the SHA-256 of these bytes as a whole. Bodies must be valid UTF-8 (the
artifact store keeps UTF-8 payloads only, E001-C I-3); the body length is given before the body,
so a body may contain any text, including lines that look like frame lines.
"""
import hashlib
import re
from typing import NamedTuple

from domain.schema import validator

_DEFS = validator.load_schema()['$defs']
ARTIFACT_KINDS = frozenset(_DEFS['artifactKind']['enum'])
_PATTERNS = {name: re.compile(_DEFS[name]['pattern'])
             for name in ('artifactId', 'runId', 'nodeId', 'protocolId', 'edgeId', 'version')}

FORMAT_LINE = b'AITS-PACKET/1'
_COUNT = re.compile(rb'^(0|[1-9][0-9]*)$')
# id and kind are matched as printable ASCII only, so a non-ASCII byte in an artifact header line
# is a malformed header (PacketFormatError), never a UnicodeDecodeError.
_ARTIFACT_LINE = re.compile(rb'^--- ARTIFACT (0|[1-9][0-9]*)/(0|[1-9][0-9]*) id=([\x21-\x7e]+) kind=([\x21-\x7e]+) '
                            rb'sha256=([0-9a-f]{64}) bytes=(0|[1-9][0-9]*)$')
END_PACKET_LINE = b'--- END PACKET'


class PacketFormatError(Exception):
    def __init__(self, problems):
        super().__init__('; '.join(problems))
        self.problems = list(problems)


class PacketHeader(NamedTuple):
    run_id: str
    edge_id: str
    from_node_id: str
    to_node_id: str
    protocol_id: str
    protocol_version: str


class PacketArtifact(NamedTuple):
    artifact_id: str
    kind: str
    content: bytes


class DecodedPacket(NamedTuple):
    header: PacketHeader
    artifacts: tuple  # of PacketArtifact, in packet order


def packet_sha256(data):
    """SHA-256 of the whole packet; equal to the artifact store's contentHash for the stored packet."""
    return hashlib.sha256(data).hexdigest()


def _matches(name, value):
    return isinstance(value, str) and bool(_PATTERNS[name].match(value))


def _header_problems(header):
    if not isinstance(header, PacketHeader):
        return [f'header must be a PacketHeader, got {type(header).__name__}']
    checks = (('runId', header.run_id, 'run'), ('edgeId', header.edge_id, 'edge'),
              ('nodeId', header.from_node_id, 'from'), ('nodeId', header.to_node_id, 'to'),
              ('protocolId', header.protocol_id, 'protocol id'), ('version', header.protocol_version, 'protocol version'))
    return [f'{label}: invalid value {value!r}' for name, value, label in checks if not _matches(name, value)]


def _artifact_problems(index, artifact):
    if not isinstance(artifact, PacketArtifact):
        return [f'artifact {index}: must be a PacketArtifact, got {type(artifact).__name__}']
    out = []
    if not _matches('artifactId', artifact.artifact_id):
        out.append(f'artifact {index}: invalid id {artifact.artifact_id!r}')
    if artifact.kind not in ARTIFACT_KINDS:
        out.append(f'artifact {index}: unknown kind {artifact.kind!r}')
    if not isinstance(artifact.content, bytes):
        out.append(f'artifact {index}: content must be bytes')
    else:
        try:
            artifact.content.decode('utf-8')
        except UnicodeDecodeError:
            out.append(f'artifact {index}: content is not valid UTF-8')
    return out


def _duplicates(ids):
    seen, dup = set(), []
    for i in ids:
        if i in seen and i not in dup:
            dup.append(i)
        seen.add(i)
    return dup


def _checkable_ids(artifacts):
    """Artifact ids that can be compared safely: string ids of PacketArtifact items. Anything else is
    already reported by _artifact_problems and is left out of the duplicate check."""
    return [a.artifact_id for a in artifacts if isinstance(a, PacketArtifact) and isinstance(a.artifact_id, str)]


def encode_packet(header, artifacts):
    """Serialize in the given order.

    Error reporting: all independent checks run on every call - the header values, each artifact's
    fields, and duplicate ids among the ids that can be compared safely - and one PacketFormatError
    lists every problem they found. (decode_packet, in contrast, stops at the first stage that fails.)"""
    artifacts = tuple(artifacts)
    problems = _header_problems(header)
    for i, a in enumerate(artifacts, 1):
        problems += _artifact_problems(i, a)
    problems += [f'duplicate artifact id {d!r}' for d in _duplicates(_checkable_ids(artifacts))]
    if problems:
        raise PacketFormatError(problems)
    n = len(artifacts)
    out = [FORMAT_LINE + b'\n',
           f'run: {header.run_id}\n'.encode('ascii'),
           f'edge: {header.edge_id}\n'.encode('ascii'),
           f'from: {header.from_node_id}\n'.encode('ascii'),
           f'to: {header.to_node_id}\n'.encode('ascii'),
           f'protocol: {header.protocol_id}@{header.protocol_version}\n'.encode('ascii'),
           f'artifacts: {n}\n'.encode('ascii')]
    for i, a in enumerate(artifacts, 1):
        out.append(f'--- ARTIFACT {i}/{n} id={a.artifact_id} kind={a.kind} '
                   f'sha256={hashlib.sha256(a.content).hexdigest()} bytes={len(a.content)}\n'.encode('ascii'))
        out.append(a.content)
        out.append(f'\n--- END ARTIFACT {i}/{n}\n'.encode('ascii'))
    out.append(END_PACKET_LINE + b'\n')
    return b''.join(out)


class _Reader:
    def __init__(self, data):
        self.data, self.pos = data, 0

    def line(self, what):
        end = self.data.find(b'\n', self.pos)
        if end < 0:
            raise PacketFormatError([f'truncated: missing line feed after {what}'])
        line, self.pos = self.data[self.pos:end], end + 1
        return line

    def take(self, n, what):
        if self.pos + n > len(self.data):
            raise PacketFormatError([f'truncated: {what} needs {n} bytes, {len(self.data) - self.pos} left'])
        chunk, self.pos = self.data[self.pos:self.pos + n], self.pos + n
        return chunk


def _field(reader, key):
    line = reader.line(f'header field {key!r}')
    prefix = key.encode('ascii') + b': '
    if not line.startswith(prefix):
        raise PacketFormatError([f'expected header field {key!r}, got {line[:40]!r}'])
    value = line[len(prefix):]
    try:
        return value.decode('ascii')
    except UnicodeDecodeError:
        raise PacketFormatError([f'header field {key!r} is not ASCII']) from None


def decode_packet(data):
    """Parse and check packet bytes. Checks the format only; agreement with Manifest/Route/Transfer/
    provenance is not checked here.

    Error reporting: parsing stops at the first stage that fails, because nothing after a broken
    frame can be read reliably. One PacketFormatError is raised; it may list several problems when
    they belong to the same stage (e.g. several invalid header values, or several invalid fields of
    one artifact). (encode_packet, in contrast, reports all independent problems at once.)"""
    if not isinstance(data, bytes):
        raise PacketFormatError([f'packet must be bytes, got {type(data).__name__}'])
    r = _Reader(data)
    first = r.line('format line')
    if first != FORMAT_LINE:
        kind = 'unknown packet version' if first.startswith(b'AITS-PACKET/') else 'not an AITS packet'
        raise PacketFormatError([f'{kind}: {first[:40]!r}'])
    run, edge, frm, to = _field(r, 'run'), _field(r, 'edge'), _field(r, 'from'), _field(r, 'to')
    protocol = _field(r, 'protocol')
    pid, sep, pver = protocol.rpartition('@')
    header = PacketHeader(run, edge, frm, to, pid if sep else protocol, pver if sep else '')
    problems = _header_problems(header)
    if problems:
        raise PacketFormatError(problems)
    count_text = _field(r, 'artifacts').encode('ascii')
    if not _COUNT.match(count_text):
        raise PacketFormatError([f'invalid artifact count {count_text!r}'])
    n = int(count_text)
    artifacts, seen = [], set()
    for i in range(1, n + 1):
        line = r.line(f'artifact {i} header')
        m = _ARTIFACT_LINE.match(line)
        if not m:
            raise PacketFormatError([f'artifact {i}: malformed header {line[:60]!r}'])
        idx, total, aid, kind, digest, size = m.groups()
        if (int(idx), int(total)) != (i, n):
            raise PacketFormatError([f'artifact {i}: header says {int(idx)}/{int(total)}, expected {i}/{n}'])
        art = PacketArtifact(aid.decode('ascii'), kind.decode('ascii'), r.take(int(size), f'artifact {i} body'))
        problems = _artifact_problems(i, art)
        if problems:
            raise PacketFormatError(problems)
        if hashlib.sha256(art.content).hexdigest() != digest.decode('ascii'):
            raise PacketFormatError([f'artifact {i}: sha256 does not match its body'])
        if r.take(1, f'artifact {i} body terminator') != b'\n':
            raise PacketFormatError([f'artifact {i}: body is longer than its declared byte count'])
        if r.line(f'artifact {i} end') != f'--- END ARTIFACT {i}/{n}'.encode('ascii'):
            raise PacketFormatError([f'artifact {i}: missing or malformed end line'])
        if art.artifact_id in seen:
            raise PacketFormatError([f'duplicate artifact id {art.artifact_id!r}'])
        seen.add(art.artifact_id)
        artifacts.append(art)
    if r.line('end of packet') != END_PACKET_LINE:
        raise PacketFormatError(['missing or malformed end-of-packet line (artifact count too small?)'])
    if r.pos != len(data):
        raise PacketFormatError([f'{len(data) - r.pos} unexpected bytes after end of packet'])
    return DecodedPacket(header, tuple(artifacts))


def canonical_order(initial_artifact_ids, artifacts):
    """Order artifacts deterministically (G-C2): first those listed in the frozen Manifest's
    initialArtifactIds, in that list's order; then all others by artifact_id in ASCII order.
    This order is chosen for determinism only, not for meaning. Nothing is added or dropped.
    Duplicate ids (in either input) are rejected."""
    initial = tuple(initial_artifact_ids)
    artifacts = tuple(artifacts)
    problems = []
    for i, a in enumerate(artifacts, 1):
        problems += _artifact_problems(i, a)
    problems += [f'initialArtifactIds: invalid id {x!r}' for x in initial if not _matches('artifactId', x)]
    if problems:
        raise PacketFormatError(problems)
    problems += [f'duplicate id {d!r} in initialArtifactIds' for d in _duplicates(initial)]
    problems += [f'duplicate artifact id {d!r}' for d in _duplicates(a.artifact_id for a in artifacts)]
    if problems:
        raise PacketFormatError(problems)
    by_id = {a.artifact_id: a for a in artifacts}
    first = [by_id[x] for x in initial if x in by_id]
    rest = sorted((a for a in artifacts if a.artifact_id not in set(initial)), key=lambda a: a.artifact_id.encode('ascii'))
    return tuple(first + rest)
