"""E001 canonical schema validator (Python standard library only).

This is deliberately NOT a general JSON Schema implementation. It interprets the
keyword subset that studio_schema_e001.json uses and refuses any other keyword,
so the schema can never rely on a rule that is silently skipped. Cross-record
rules that JSON Schema cannot express (reference integrity, hashes, isolation
ceilings, ...) are implemented below as semantic rules SR-01..SR-16.

The supported scope is documented in docs/SCHEMA_VALIDATION_SCOPE.md and every
rule is exercised by tests/unit/test_schema.py.

Usage:
    python -m domain.schema.validator path/to/bundle.json   (run from evolution-e001/)
"""
import datetime
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import NamedTuple

SCHEMA_PATH = Path(__file__).with_name('studio_schema_e001.json')
SUPPORTED_SCHEMA_VERSIONS = ('E001/1.0',)

# Keywords that carry no validation meaning.
ANNOTATION_KEYWORDS = {'$schema', '$id', 'title', 'description', '$comment'}
# Keywords the validator enforces.
ASSERTION_KEYWORDS = {
    '$ref', 'type', 'properties', 'required', 'additionalProperties', 'enum', 'const',
    'pattern', 'items', 'minItems', 'maxItems', 'minimum', 'minLength', 'format',
    'x-ref', 'x-ref-versioned',
}
ROOT_ONLY_KEYWORDS = {'$defs', 'x-entities'}
SUPPORTED_FORMATS = {'date-time'}
SUPPORTED_TYPES = {'object', 'array', 'string', 'integer', 'number', 'boolean', 'null'}

MANUAL_PROVIDER_KINDS = {'MANUAL_CLIPBOARD', 'MANUAL_FILE'}
_DATE_TIME = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$')


class SchemaDefinitionError(Exception):
    """The schema file itself uses something this validator does not support."""


class Issue(NamedTuple):
    code: str
    path: str
    message: str
    rule: str = ''


# ---------- schema loading ----------

def load_schema(path=SCHEMA_PATH):
    schema = json.loads(Path(path).read_text(encoding='utf-8'))
    _check_keywords(schema, '#', root=True)
    return schema


def _check_keywords(node, path, root=False):
    if not isinstance(node, dict):
        raise SchemaDefinitionError(f'{path}: subschema must be an object')
    allowed = ANNOTATION_KEYWORDS | ASSERTION_KEYWORDS | (ROOT_ONLY_KEYWORDS if root else set())
    unknown = set(node) - allowed
    if unknown:
        raise SchemaDefinitionError(f'{path}: unsupported keyword(s) {sorted(unknown)}')
    if 'additionalProperties' in node and not isinstance(node['additionalProperties'], bool):
        raise SchemaDefinitionError(f'{path}: additionalProperties must be true/false')
    if 'format' in node and node['format'] not in SUPPORTED_FORMATS:
        raise SchemaDefinitionError(f'{path}: unsupported format {node["format"]!r}')
    if '$ref' in node and not str(node['$ref']).startswith('#/$defs/'):
        raise SchemaDefinitionError(f'{path}: only local #/$defs/ references are supported')
    types = node.get('type')
    for t in (types if isinstance(types, list) else [types] if types else []):
        if t not in SUPPORTED_TYPES:
            raise SchemaDefinitionError(f'{path}: unsupported type {t!r}')
    for key, sub in node.get('properties', {}).items():
        _check_keywords(sub, f'{path}/properties/{key}')
    if 'items' in node:
        _check_keywords(node['items'], f'{path}/items')
    for key, sub in node.get('$defs', {}).items():
        _check_keywords(sub, f'{path}/$defs/{key}')


# ---------- structural validation ----------

class _Walker:
    def __init__(self, schema):
        self.defs = schema.get('$defs', {})
        self.issues = []
        self.refs = []            # (entity, id, path)
        self.versioned_refs = []  # (entity, id, version, path)

    def resolve(self, ref):
        name = ref[len('#/$defs/'):]
        if name not in self.defs:
            raise SchemaDefinitionError(f'unresolvable $ref {ref}')
        return self.defs[name]

    def add(self, code, path, message):
        self.issues.append(Issue(code, path, message))

    def check(self, value, sub, path):
        if '$ref' in sub:
            self.check(value, self.resolve(sub['$ref']), path)
        if 'type' in sub and not _type_ok(value, sub['type']):
            self.add('TYPE', path, f'expected {sub["type"]}, got {_type_name(value)}')
            return
        if 'const' in sub and value != sub['const']:
            self.add('CONST', path, f'must be {sub["const"]!r}')
        if 'enum' in sub and value not in sub['enum']:
            self.add('ENUM', path, f'{value!r} is not one of {sub["enum"]}')
        if isinstance(value, str):
            if 'pattern' in sub and not re.search(sub['pattern'], value):
                self.add('PATTERN', path, f'{value!r} does not match {sub["pattern"]}')
            if 'minLength' in sub and len(value) < sub['minLength']:
                self.add('MIN_LENGTH', path, f'shorter than {sub["minLength"]}')
            if sub.get('format') == 'date-time' and not _date_time_ok(value):
                self.add('FORMAT', path, f'{value!r} is not an RFC 3339 date-time with timezone')
            if 'x-ref' in sub:
                self.refs.append((sub['x-ref'], value, path))
        if isinstance(value, (int, float)) and not isinstance(value, bool) and 'minimum' in sub and value < sub['minimum']:
            self.add('MINIMUM', path, f'{value} < {sub["minimum"]}')
        if isinstance(value, list):
            if 'minItems' in sub and len(value) < sub['minItems']:
                self.add('MIN_ITEMS', path, f'fewer than {sub["minItems"]} items')
            if 'maxItems' in sub and len(value) > sub['maxItems']:
                self.add('MAX_ITEMS', path, f'more than {sub["maxItems"]} items')
            if 'items' in sub:
                for i, item in enumerate(value):
                    self.check(item, sub['items'], f'{path}[{i}]')
        if isinstance(value, dict):
            props = sub.get('properties', {})
            for key in sub.get('required', []):
                if key not in value:
                    self.add('REQUIRED', f'{path}.{key}', 'missing required field')
            if sub.get('additionalProperties') is False:
                for key in value:
                    if key not in props:
                        self.add('ADDITIONAL', f'{path}.{key}', 'field not allowed by schema')
            for key, child in props.items():
                if key in value:
                    self.check(value[key], child, f'{path}.{key}')
            if 'x-ref-versioned' in sub and isinstance(value.get('id'), str) and isinstance(value.get('version'), str):
                self.versioned_refs.append((sub['x-ref-versioned'], value['id'], value['version'], path))


def _type_ok(value, types):
    return any(_is_type(value, t) for t in (types if isinstance(types, list) else [types]))


def _is_type(value, t):
    if t == 'integer':
        return isinstance(value, int) and not isinstance(value, bool)
    if t == 'number':
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, {'object': dict, 'array': list, 'string': str, 'boolean': bool, 'null': type(None)}[t])


def _type_name(value):
    return {dict: 'object', list: 'array', str: 'string', bool: 'boolean', type(None): 'null'}.get(type(value), type(value).__name__)


def _date_time_ok(s):
    if not _DATE_TIME.match(s):
        return False
    try:
        return datetime.datetime.fromisoformat(s).tzinfo is not None
    except ValueError:
        return False


def validate_record(entity, record, schema=None):
    """Structural check of one record. Reference integrity needs a bundle (validate_bundle)."""
    schema = schema or load_schema()
    meta = schema['x-entities'].get(entity)
    if meta is None:
        return [Issue('UNKNOWN_ENTITY', entity, f'unknown entity type {entity!r}')]
    version = record.get('schemaVersion') if isinstance(record, dict) else None
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        return [Issue('UNSUPPORTED_SCHEMA_VERSION', f'{entity}.schemaVersion', f'unsupported schema version {version!r}')]
    w = _Walker(schema)
    w.check(record, schema['$defs'][meta['def']], entity)
    return w.issues


def validate_bundle(bundle, schema=None):
    """Full check: structure, reference integrity and semantic rules SR-01..SR-16."""
    schema = schema or load_schema()
    version = bundle.get('schemaVersion') if isinstance(bundle, dict) else None
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        # Stop here: nothing else is interpreted for an unknown version.
        return [Issue('UNSUPPORTED_SCHEMA_VERSION', 'schemaVersion', f'unsupported schema version {version!r}')]
    w = _Walker(schema)
    w.check(bundle, schema, 'bundle')
    if any(i.code == 'TYPE' and i.path in ('bundle', 'bundle.records') for i in w.issues):
        return w.issues
    records = {k: [r for r in v if isinstance(r, dict)] for k, v in bundle.get('records', {}).items() if isinstance(v, list)}
    idx = _Index(schema, records)
    issues = list(w.issues)
    issues += idx.duplicates()
    for entity, rid, path in w.refs:
        if rid not in idx.ids.get(entity, {}):
            issues.append(Issue('REF', path, f'{entity} {rid!r} does not exist in this bundle'))
    for entity, rid, ver, path in w.versioned_refs:
        if (rid, ver) not in idx.versioned.get(entity, {}):
            issues.append(Issue('VERSIONED_REF', path, f'{entity} {rid!r} version {ver!r} does not exist in this bundle'))
    issues += _semantic_rules(idx)
    return issues


class _Index:
    def __init__(self, schema, records):
        self.records = records
        self.meta = schema['x-entities']
        self.ids, self.versioned = {}, {}
        for entity, meta in self.meta.items():
            for r in records.get(entity, []):
                rid = r.get(meta['idField'])
                if not isinstance(rid, str):
                    continue
                self.ids.setdefault(entity, {}).setdefault(rid, r)
                if meta['versioned'] and isinstance(r.get('version'), str):
                    self.versioned.setdefault(entity, {}).setdefault((rid, r['version']), r)

    def duplicates(self):
        out = []
        for entity, meta in self.meta.items():
            seen = set()
            for i, r in enumerate(self.records.get(entity, [])):
                key = (r.get(meta['idField']), r.get('version')) if meta['versioned'] else r.get(meta['idField'])
                if key in seen:
                    out.append(Issue('DUPLICATE_ID', f'bundle.records.{entity}[{i}]', f'duplicate {entity} id {key!r}', 'SR-01'))
                seen.add(key)
        return out

    def get(self, entity, rid):
        return self.ids.get(entity, {}).get(rid)

    def get_versioned(self, entity, ref):
        if not isinstance(ref, dict):
            return None
        return self.versioned.get(entity, {}).get((ref.get('id'), ref.get('version')))


# ---------- semantic rules ----------

def _semantic_rules(idx):
    out = []

    def bad(rule, path, message, code='SEMANTIC'):
        out.append(Issue(code, path, message, rule))

    recs = idx.records

    # SR-02 manual providers never claim ENFORCED isolation
    for i, p in enumerate(recs.get('provider', [])):
        if p.get('kind') in MANUAL_PROVIDER_KINDS and p.get('maxIsolationAssurance') == 'ENFORCED':
            bad('SR-02', f'provider[{i}].maxIsolationAssurance', 'a manual provider cannot claim ENFORCED isolation')
    for i, m in enumerate(recs.get('runManifest', [])):
        for j, p in enumerate(m.get('providers', []) if isinstance(m.get('providers'), list) else []):
            if isinstance(p, dict) and p.get('kind') in MANUAL_PROVIDER_KINDS and p.get('maxIsolationAssurance') == 'ENFORCED':
                bad('SR-02', f'runManifest[{i}].providers[{j}]', 'a manual provider cannot claim ENFORCED isolation')
    for i, t in enumerate(recs.get('transfer', [])):
        if t.get('method') in MANUAL_PROVIDER_KINDS and t.get('isolationAssurance') == 'ENFORCED':
            bad('SR-02', f'transfer[{i}].isolationAssurance', 'a manual transfer cannot claim ENFORCED isolation')

    # SR-03 source verification status is consistent with its hashes
    for i, s in enumerate(recs.get('source', [])):
        _check_source_status(s, f'source[{i}]', bad)
    for i, m in enumerate(recs.get('runManifest', [])):
        for j, s in enumerate(m.get('sources', []) if isinstance(m.get('sources'), list) else []):
            if isinstance(s, dict):
                _check_source_status(s, f'runManifest[{i}].sources[{j}]', bad)

    # SR-04 artifact content: exactly one location, exact-byte hash for inline content
    for i, a in enumerate(recs.get('artifact', [])):
        has_inline, has_loc = 'content' in a, 'contentLocation' in a
        if has_inline == has_loc:
            bad('SR-04', f'artifact[{i}]', 'exactly one of content / contentLocation is required')
        elif has_inline and isinstance(a.get('content'), str):
            data = a['content'].encode('utf-8')
            if a.get('byteSize') != len(data):
                bad('SR-04', f'artifact[{i}].byteSize', f'byteSize {a.get("byteSize")} != {len(data)} stored bytes', 'HASH_MISMATCH')
            if a.get('contentHash') != hashlib.sha256(data).hexdigest():
                bad('SR-04', f'artifact[{i}].contentHash', 'contentHash does not match the SHA-256 of the stored bytes', 'HASH_MISMATCH')

    # SR-05 transfer packet is a PACKET artifact and the hash matches it
    for i, t in enumerate(recs.get('transfer', [])):
        pkt = idx.get('artifact', t.get('packetArtifactId'))
        if pkt is not None:
            if pkt.get('kind') != 'PACKET':
                bad('SR-05', f'transfer[{i}].packetArtifactId', 'packet artifact must be of kind PACKET')
            if t.get('packetHash') != pkt.get('contentHash'):
                bad('SR-05', f'transfer[{i}].packetHash', 'packetHash differs from the packet artifact contentHash', 'HASH_MISMATCH')

    # SR-06 observation is bound to its transfer and a RAW_RESPONSE artifact
    for i, o in enumerate(recs.get('observation', [])):
        raw = idx.get('artifact', o.get('rawResponseArtifactId'))
        if raw is not None and raw.get('kind') != 'RAW_RESPONSE':
            bad('SR-06', f'observation[{i}].rawResponseArtifactId', 'must reference a RAW_RESPONSE artifact')
        t = idx.get('transfer', o.get('transferId'))
        if t is not None:
            if t.get('runId') != o.get('runId'):
                bad('SR-06', f'observation[{i}].runId', 'observation and its transfer belong to different runs')
            if t.get('toNodeId') != o.get('observerNodeId'):
                bad('SR-06', f'observation[{i}].observerNodeId', 'observer is not the destination of the transfer')

    # SR-07 synthesis findings cite only its own input observations, all from the same run
    for i, s in enumerate(recs.get('synthesis', [])):
        inputs = set(s.get('inputObservationIds') or [])
        for oid in inputs:
            o = idx.get('observation', oid)
            if o is not None and o.get('runId') != s.get('runId'):
                bad('SR-07', f'synthesis[{i}].inputObservationIds', f'{oid} belongs to another run')
        for section in ('common', 'differences', 'unknowns', 'contradictions', 'executionNeeds', 'nextQuestions'):
            for j, item in enumerate(s.get(section) or []):
                if isinstance(item, dict):
                    extra = set(item.get('observationIds') or []) - inputs
                    if extra:
                        bad('SR-07', f'synthesis[{i}].{section}[{j}]', f'cites observations outside inputObservationIds: {sorted(extra)}')

    # SR-08 event sequence is unique and strictly increasing in log order
    last = 0
    for i, e in enumerate(recs.get('event', [])):
        seq = e.get('sequence')
        if isinstance(seq, int) and not isinstance(seq, bool):
            if seq <= last:
                bad('SR-08', f'event[{i}].sequence', f'sequence {seq} is not greater than {last}')
            last = max(last, seq)

    # SR-09 gate decisions are made by a HUMAN node
    for i, g in enumerate(recs.get('gate', [])):
        n = idx.get('node', g.get('decidedBy'))
        if n is not None and n.get('kind') != 'HUMAN':
            bad('SR-09', f'gate[{i}].decidedBy', 'a gate must be decided by a HUMAN node')

    # SR-10 run and manifest point at each other and at the same trial
    for i, r in enumerate(recs.get('run', [])):
        m = idx.get('runManifest', r.get('runManifestId'))
        if m is not None:
            if m.get('runId') != r.get('runId'):
                bad('SR-10', f'run[{i}].runManifestId', 'manifest belongs to a different run')
            if m.get('trialId') != r.get('trialId'):
                bad('SR-10', f'run[{i}].trialId', 'run and manifest name different trials')

    # SR-11 route bindings cover the pattern slots exactly once; edges join bound or SYSTEM nodes
    for i, rt in enumerate(recs.get('route', [])):
        pat = idx.get_versioned('pattern', rt.get('patternRef'))
        bindings = [b for b in rt.get('slotBindings') or [] if isinstance(b, dict)]
        if pat is not None:
            slots = [s.get('slotId') for s in pat.get('slots') or [] if isinstance(s, dict)]
            bound = [b.get('slotId') for b in bindings]
            if sorted(bound) != sorted(slots):
                bad('SR-11', f'route[{i}].slotBindings', f'bindings {sorted(bound)} do not cover pattern slots {sorted(slots)} exactly once')
        bound_nodes = {b.get('nodeId') for b in bindings}
        edge_ids = set()
        for j, e in enumerate(rt.get('edges') or []):
            if not isinstance(e, dict):
                continue
            if e.get('edgeId') in edge_ids:
                bad('SR-11', f'route[{i}].edges[{j}].edgeId', 'duplicate edgeId within route')
            edge_ids.add(e.get('edgeId'))
            for end in ('fromNodeId', 'toNodeId'):
                nid = e.get(end)
                n = idx.get('node', nid)
                if nid not in bound_nodes and (n is None or n.get('kind') != 'SYSTEM'):
                    bad('SR-11', f'route[{i}].edges[{j}].{end}', f'{nid} is neither bound to a slot nor a SYSTEM node')

    # SR-12 a transfer follows an edge of the route frozen for its run
    for i, t in enumerate(recs.get('transfer', [])):
        run = idx.get('run', t.get('runId'))
        man = idx.get('runManifest', run.get('runManifestId')) if run else None
        rt = idx.get_versioned('route', man.get('routeRef')) if man else None
        if rt is None:
            continue
        edge = next((e for e in rt.get('edges') or [] if isinstance(e, dict) and e.get('edgeId') == t.get('edgeId')), None)
        if edge is None:
            bad('SR-12', f'transfer[{i}].edgeId', f'edge {t.get("edgeId")!r} is not in the run\'s route')
        elif (edge.get('fromNodeId'), edge.get('toNodeId')) != (t.get('fromNodeId'), t.get('toNodeId')):
            bad('SR-12', f'transfer[{i}]', 'transfer endpoints differ from its route edge')

    # SR-13 manifest role assignments agree with the pattern slots and route bindings; frozen nodes exist
    for i, m in enumerate(recs.get('runManifest', [])):
        pat = idx.get_versioned('pattern', m.get('patternRef'))
        rt = idx.get_versioned('route', m.get('routeRef'))
        slot_roles = {s.get('slotId'): s.get('role') for s in (pat.get('slots') or []) if isinstance(s, dict)} if pat else None
        binds = {b.get('slotId'): (b.get('nodeId'), b.get('providerId')) for b in (rt.get('slotBindings') or []) if isinstance(b, dict)} if rt else None
        for j, a in enumerate(m.get('roleAssignments') or []):
            if not isinstance(a, dict):
                continue
            if slot_roles is not None and slot_roles.get(a.get('slotId')) != a.get('role'):
                bad('SR-13', f'runManifest[{i}].roleAssignments[{j}]', 'role differs from the pattern slot role')
            if binds is not None and binds.get(a.get('slotId')) != (a.get('nodeId'), a.get('providerId')):
                bad('SR-13', f'runManifest[{i}].roleAssignments[{j}]', 'assignment differs from the route slot binding')
        for j, n in enumerate(m.get('nodes') or []):
            if isinstance(n, dict) and idx.get('node', n.get('nodeId')) is None:
                bad('SR-13', f'runManifest[{i}].nodes[{j}]', f'frozen node {n.get("nodeId")!r} has no node record')
        for j, p in enumerate(m.get('providers') or []):
            if isinstance(p, dict) and idx.get('provider', p.get('providerId')) is None:
                bad('SR-13', f'runManifest[{i}].providers[{j}]', f'frozen provider {p.get("providerId")!r} has no provider record')

    # SR-14 L1 evaluations are made by the SYSTEM, never by an AI self-report
    for i, ev in enumerate(recs.get('evaluation', [])):
        n = idx.get('node', ev.get('evaluatorNodeId'))
        if n is not None and n.get('kind') != 'SYSTEM':
            bad('SR-14', f'evaluation[{i}].evaluatorNodeId', 'an L1 evaluation must be made by a SYSTEM node')

    # SR-15 trial, route and manifest agree on pattern / route / protocol versions
    for i, tr in enumerate(recs.get('trial', [])):
        rt = idx.get_versioned('route', tr.get('routeRef'))
        if rt is not None:
            if rt.get('patternRef') != tr.get('patternRef'):
                bad('SR-15', f'trial[{i}].patternRef', 'trial and route name different patterns')
            if rt.get('protocolRef') != tr.get('protocolRef'):
                bad('SR-15', f'trial[{i}].protocolRef', 'trial and route name different protocols')
    for i, m in enumerate(recs.get('runManifest', [])):
        tr = idx.get('trial', m.get('trialId'))
        if tr is not None:
            for ref in ('patternRef', 'routeRef', 'protocolRef'):
                if m.get(ref) != tr.get(ref):
                    bad('SR-15', f'runManifest[{i}].{ref}', f'manifest {ref} differs from its trial')
            if m.get('caseId') != tr.get('caseId'):
                bad('SR-15', f'runManifest[{i}].caseId', 'manifest case differs from its trial')

    # SR-16 event / gate / evaluation targets exist (a gate is bound to one concrete target)
    for entity in ('event', 'gate', 'evaluation'):
        for i, r in enumerate(recs.get(entity, [])):
            tt, tid = r.get('targetType'), r.get('targetId')
            if isinstance(tt, str) and tt in idx.meta and idx.get(tt, tid) is None:
                bad('SR-16', f'{entity}[{i}].targetId', f'target {tt} {tid!r} does not exist in this bundle')
    return out


def _check_source_status(s, path, bad):
    st, v, c = s.get('verificationStatus'), s.get('verifiedHash'), s.get('claimedHash')
    ok = {
        'VERIFIED': v is not None and (c is None or c == v),
        'MISMATCH': v is not None and c is not None and c != v,
        'CLAIMED_ONLY': v is None and c is not None,
        'UNVERIFIED': v is None,
        'UNKNOWN': v is None,
    }.get(st, True)
    if not ok:
        bad('SR-03', f'{path}.verificationStatus', f'status {st} is inconsistent with verifiedHash/claimedHash')


def main(argv):
    if len(argv) != 2:
        print('usage: python -m domain.schema.validator BUNDLE.json')
        return 2
    issues = validate_bundle(json.loads(Path(argv[1]).read_text(encoding='utf-8')))
    for i in issues:
        print(f'{i.code:<28} {i.rule:<6} {i.path}: {i.message}')
    print('VALID' if not issues else f'INVALID ({len(issues)} issue(s))')
    return 0 if not issues else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv))
