"""AI Team Studio — the record kept by the relay (課題 3-A). Standard library only.

Contract: ../RECORD_API_3A.md 0.6.1. Tests: test_record_api.py (§7).

Why it exists:
  * The Studio record lived only in one browser's storage, so two Studios or a cleared storage could lose it.
    Here the record is files next to the relay: _record/cases/<id>.json is the one copy of a Case that counts,
    every earlier version is kept in _record/history/<id>/<rev>.json, and the settings are _record/settings.json.
  * A write names the version it was based on (baseRev) and a request id. A stale version is refused (409);
    the same request sent again gets the first answer back, even after a restart (the index is rebuilt from the
    version files at start).
  * Nothing outside _record is read or written: every path is checked for links, junctions, hard links and its
    real place, and a file is checked again on the handle it was opened with (§3.5).

The HTTP side (gate, routing) is in studio_proxy.py; this module holds the files, the checks and the body framing.
"""
import datetime, hashlib, json, os, re, stat, sys, tempfile, threading, time
from pathlib import Path

LOCK_WAIT = 10                    # seconds to wait for the record lock before answering 503 busy (§6.1)
BODY_DEADLINE = 10                # absolute deadline for reading a POST body, from its first byte (§4.1)
MAX_BODY = 2 * 1024 * 1024        # one write (利用者の決定 §8-2)
REPLACE_ATTEMPTS, REPLACE_WAIT = 5, 0.2   # Windows: replacing a file another process holds open can fail for a moment
LINGER_SECONDS = 1.0              # after a POST answer, unread bytes are discarded (never parsed) for this long

CASE_ID = re.compile(r'CASE-\d{8}-\d{3}(-[a-z0-9]{6})?')
REQUEST_ID = re.compile(r'[A-Za-z0-9-]{8,64}')
CREATED = re.compile(r'(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})(\.\d{3})?Z')
SAVED_AT = re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z')
CONTENT_LENGTH = re.compile(r'[0-9]{1,8}')
HISTORY_NAME = re.compile(r'([1-9][0-9]{0,9})\.json')
SETTINGS = '_settings'
FORMATS = {'case': 'ats-case-1', 'settings': 'ats-settings-1'}
VERSION_KEYS = ('format', 'id', 'rev', 'savedAt', 'requestId', 'sha256', 'metaSha256')
FIXED_PATHS = (('', 'dir'), ('cases', 'dir'), ('history', 'dir'), ('history/_settings', 'dir'), ('.lock', 'file'))
REPARSE_POINT = getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400)
LOCK_HELD = '別の中継が同じ記録を使っています'


def _fault(stage):
    """Test hook (never set by any configuration): called at the stages of a write, see test_record_api.py."""


def _before_open(path):
    """Test hook: called after a path has been checked and right before it is opened (R-16d)."""


class RecordStartError(Exception):
    """The relay must not start on this record (§3.3.1, §3.5, §6.2)."""


class Answer(Exception):
    """An HTTP answer decided inside the record code."""
    def __init__(self, code, body):
        super().__init__(f'{code} {body}')
        self.code, self.body = code, body


class PathBad(Exception):
    """A path that is a link, outside _record, of the wrong kind, or hard-linked (§3.5)."""
    def __init__(self, rel, why):
        super().__init__(f'{rel or "_record"}: {why}')
        self.rel = rel


class Corrupt(Exception):
    """A version file that does not satisfy §3.3."""
    def __init__(self, rel, why):
        super().__init__(f'{rel}: {why}')
        self.rel = rel


class Fatal(Exception):
    """A path the whole record depends on went wrong while running (§3.5 table, first row)."""


# ---- JSON and hashes (§2.1) ----
def _unique_keys(pairs):
    out = {}
    for k, v in pairs:
        if k in out:
            raise ValueError(f'duplicate key {k!r}')
        out[k] = v
    return out


def _no_constant(name):
    raise ValueError(f'{name} is not allowed')


def parse_strict(text):
    """json.loads that refuses NaN / Infinity and repeated keys. Key order is kept."""
    return json.loads(text, object_pairs_hook=_unique_keys, parse_constant=_no_constant)


def canonical(obj):
    """The canonical bytes. Raises ValueError for numbers out of range and UnicodeEncodeError (a ValueError)
    for unpaired surrogates."""
    return json.dumps(obj, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')


def sha256_of(obj):
    return hashlib.sha256(canonical(obj)).hexdigest()


def meta_sha256(fmt, id_, rev, saved_at, request_id, body_sha):
    return sha256_of([fmt, id_, rev, saved_at, request_id, body_sha])


def version_problem(doc, key, kind, rev=None):
    """None if doc is a valid version file of `key` (and of `rev`, for a history file), else the reason (§3.3)."""
    if not isinstance(doc, dict) or set(doc) != set(VERSION_KEYS) | {kind}:
        return 'fields differ from the version format'
    if doc['format'] != FORMATS[kind]:
        return 'format'
    if doc['id'] != key:
        return 'id differs from the file name'
    body = doc[kind]
    if not isinstance(body, dict):
        return f'{kind} is not an object'
    if kind == 'case' and body.get('id') != key:
        return 'case.id differs from the file name'
    r = doc['rev']
    if type(r) is not int or r < 1:
        return 'rev'
    if rev is not None and r != rev:
        return 'rev differs from the file name'
    if not isinstance(doc['savedAt'], str) or not SAVED_AT.fullmatch(doc['savedAt']):
        return 'savedAt'
    if not isinstance(doc['requestId'], str) or not REQUEST_ID.fullmatch(doc['requestId']):
        return 'requestId'
    try:
        h = sha256_of(body)
    except ValueError:
        return f'{kind} cannot be hashed'
    if doc['sha256'] != h:
        return 'sha256'
    if doc['metaSha256'] != meta_sha256(doc['format'], key, r, doc['savedAt'], doc['requestId'], h):
        return 'metaSha256'
    return None


def created_key(value):
    """createdAt as a sortable string (UTC, milliseconds), or None when it is not the contract's form (§2.2)."""
    m = CREATED.fullmatch(value) if isinstance(value, str) else None
    if not m:
        return None
    try:
        datetime.datetime.strptime(m.group(1) + 'T' + m.group(2), '%Y-%m-%dT%H:%M:%S')
    except ValueError:
        return None
    return f'{m.group(1)}T{m.group(2)}{m.group(3) or ".000"}Z'


def _now():
    t = datetime.datetime.now(datetime.timezone.utc)
    return t.strftime('%Y-%m-%dT%H:%M:%S.') + f'{t.microsecond // 1000:03d}Z'


def _is_temp(name):
    return name.startswith('.') and name.endswith('.tmp')


def _is_link(st):
    return stat.S_ISLNK(st.st_mode) or bool(getattr(st, 'st_file_attributes', 0) & REPARSE_POINT)


def _slot(key):
    """(kind, current file, history folder) of a Case or of the settings."""
    if key == SETTINGS:
        return 'settings', 'settings.json', 'history/_settings'
    return 'case', f'cases/{key}.json', f'history/{key}'


def _os_lock(fd, unlock=False):
    if os.name == 'nt':
        import msvcrt
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK if unlock else msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_UN if unlock else fcntl.LOCK_EX | fcntl.LOCK_NB)


# ---- request bodies (§4, §4.1) ----
def _json_content_type(value):
    parts = [p.strip() for p in value.split(';')]
    if parts[0].lower() != 'application/json':
        return False
    for p in parts[1:]:
        k, sep, v = p.partition('=')
        if not sep or k.strip().lower() != 'charset' or v.strip().strip('"').lower() != 'utf-8':
            return False
    return True


def read_body(headers, rfile, sock):
    """The JSON body of a /record POST, read exactly as §4.1 says, or Answer. Nothing beyond Content-Length is read."""
    if headers.get_all('Transfer-Encoding'):
        raise Answer(400, {'error': 'Transfer-Encoding is not accepted'})
    lengths = headers.get_all('Content-Length') or []
    if not lengths:
        raise Answer(411, {'error': 'Content-Length is required'})
    if len(lengths) != 1 or not CONTENT_LENGTH.fullmatch(lengths[0]):
        raise Answer(400, {'error': 'one Content-Length of up to 8 digits is required'})
    n = int(lengths[0])
    if n > MAX_BODY:
        raise Answer(413, {'error': f'too large ({n} bytes, limit {MAX_BODY})'})
    types = headers.get_all('Content-Type') or []
    if len(types) != 1 or not _json_content_type(types[0]):
        raise Answer(415, {'error': 'Content-Type must be application/json (charset=utf-8 only)'})
    deadline = time.monotonic() + BODY_DEADLINE
    buf = bytearray()
    try:
        while len(buf) < n:
            left = deadline - time.monotonic()
            if left <= 0:
                raise Answer(408, {'error': 'the body did not arrive in time'})
            sock.settimeout(left)
            chunk = rfile.read1(min(n - len(buf), 65536))
            if not chunk:
                raise Answer(400, {'error': 'the body is shorter than Content-Length'})
            buf += chunk
    except TimeoutError:
        raise Answer(408, {'error': 'the body did not arrive in time'})
    data = bytes(buf)
    if data.startswith(b'\xef\xbb\xbf'):
        raise Answer(400, {'error': 'a byte order mark is not accepted'})
    try:
        return parse_strict(data.decode('utf-8'))
    except ValueError as e:
        raise Answer(400, {'error': f'not JSON as accepted here: {e}'})


def _bad(why):
    return Answer(400, {'error': why})


def _check_common(j, body_key, keys):
    if not isinstance(j, dict) or set(j) != set(keys):
        raise _bad(f'the body must have exactly {", ".join(keys)}')
    base = j['baseRev']
    if type(base) is not int or base < 0:
        raise _bad('baseRev must be an integer of 0 or more')
    rid = j['requestId']
    if not isinstance(rid, str) or not REQUEST_ID.fullmatch(rid):
        raise _bad('requestId must match [A-Za-z0-9-]{8,64}')
    body = j[body_key]
    if not isinstance(body, dict):
        raise _bad(f'{body_key} must be an object')
    try:
        return base, rid, body, sha256_of(body)
    except ValueError as e:
        raise _bad(f'{body_key} cannot be written as JSON: {e}')


def valid_case_id(id_):
    return isinstance(id_, str) and bool(CASE_ID.fullmatch(id_))


# ---- the store ----
class RecordStore:
    def __init__(self, root):
        self.root = Path(root)
        self.real = None
        self.lock = threading.Lock()
        self.failed = None          # set when a path the record depends on went wrong while running
        self.index = {}             # key -> {requestId: (rev, sha256)}, built at start (§3.4)
        self.bad = {}               # key -> {'problem': 'corrupt' | 'orphan_history', 'files': [...]}
        self.unknown = []           # files and folders not tied to any Case (§3.3.1)
        self.hist_files = self.hist_bytes = 0
        self._lock_fd = self._lock_stat = None

    # ---- paths (§3.5) ----
    def _p(self, rel):
        return self.root / rel if rel else self.root

    def _check(self, rel, kind):
        """lstat of a path that must be a plain folder / single-link file in its place inside _record.
        FileNotFoundError when it is missing, PathBad when it is anything else."""
        path = self._p(rel)
        st = os.lstat(path)
        if _is_link(st):
            raise PathBad(rel, 'is a link or junction')
        if not (stat.S_ISDIR(st.st_mode) if kind == 'dir' else stat.S_ISREG(st.st_mode)):
            raise PathBad(rel, f'is not a {"folder" if kind == "dir" else "plain file"}')
        if kind == 'file' and st.st_nlink != 1:
            raise PathBad(rel, 'is hard-linked')
        want = self.real / rel if rel else self.real
        if os.path.normcase(str(path.resolve(strict=True))) != os.path.normcase(str(want)):
            raise PathBad(rel, 'is not where it should be inside _record')
        return st

    def _read(self, rel):
        """The bytes of a checked file, read through a handle that is checked to be the same file (§3.5)."""
        st = self._check(rel, 'file')
        path = self._p(rel)
        _before_open(path)
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_BINARY', 0))
        try:
            fs = os.fstat(fd)
            if (fs.st_dev, fs.st_ino) != (st.st_dev, st.st_ino) or not stat.S_ISREG(fs.st_mode) or fs.st_nlink != 1:
                raise PathBad(rel, 'the file opened is not the file checked')
            chunks = []
            while True:
                b = os.read(fd, 1 << 20)
                if not b:
                    return b''.join(chunks)
                chunks.append(b)
        finally:
            os.close(fd)

    def _version(self, rel, key, kind, rev=None):
        """(doc, bytes) of a version file that satisfies §3.3, else Corrupt. FileNotFoundError when missing."""
        try:
            data = self._read(rel)
        except PathBad as e:
            raise Corrupt(rel, str(e))
        try:
            text = data.decode('utf-8')
            if text.startswith('﻿'):
                raise ValueError('byte order mark')
            doc = parse_strict(text)
        except ValueError as e:
            raise Corrupt(rel, f'not JSON: {e}')
        problem = version_problem(doc, key, kind, rev)
        if problem:
            raise Corrupt(rel, problem)
        return doc, data

    def _write_atomic(self, rel, data, final=False):
        """Publish bytes under rel only when complete (temporary file in the same folder, fsync, os.replace)."""
        path = self._p(rel)
        fd, tmp = tempfile.mkstemp(prefix='.' + path.name + '.', suffix='.tmp', dir=path.parent)
        try:
            with os.fdopen(fd, 'wb') as f:
                fs = os.fstat(f.fileno())
                if not stat.S_ISREG(fs.st_mode) or fs.st_nlink != 1:
                    raise PathBad(rel, 'the temporary file is not a plain file')
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            if final:
                _fault('before_replace')
            for attempt in range(REPLACE_ATTEMPTS):
                try:
                    os.replace(tmp, path)
                    break
                except PermissionError:
                    if attempt == REPLACE_ATTEMPTS - 1:
                        raise
                    time.sleep(REPLACE_WAIT)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
        self._check(rel, 'file')

    def _mark(self, key, problem, *files):
        b = self.bad.setdefault(key, {'problem': problem, 'files': []})
        if problem == 'corrupt':
            b['problem'] = 'corrupt'
        for f in files:
            if f not in b['files']:
                b['files'].append(f)

    def _corrupt(self, key):
        b = self.bad[key]
        return Answer(409, {'error': 'record_corrupt', 'problem': b['problem'], 'files': list(b['files'])})

    # ---- start (§3.3.1, §3.4, §6.2) ----
    def open(self):
        t0 = time.monotonic()
        try:
            self._prepare()
            n = self._scan()
        except PathBad as e:
            self.close()
            raise RecordStartError(str(e))
        except BaseException:
            self.close()
            raise
        cases = len([k for k in self.index if k != SETTINGS])
        print(f'[record] {self.root}: files={n} seconds={time.monotonic() - t0:.3f} cases={cases} '
              f'problems={len(self.bad)} unknown={len(self.unknown)}', file=sys.stderr)

    def _prepare(self):
        try:
            st = os.lstat(self.root)
        except FileNotFoundError:
            self.root.mkdir(parents=True)
            st = os.lstat(self.root)
        if _is_link(st) or not stat.S_ISDIR(st.st_mode):
            raise RecordStartError(f'{self.root} is a link or not a folder')
        self.real = self.root.resolve(strict=True)
        for rel in ('cases', 'history', 'history/_settings'):
            if not os.path.lexists(self._p(rel)):
                os.mkdir(self._p(rel))
            self._check(rel, 'dir')
        path = self._p('.lock')
        if os.path.lexists(path):
            self._check('.lock', 'file')
        _before_open(path)
        fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, 'O_BINARY', 0), 0o644)
        try:
            fs = os.fstat(fd)
            if not stat.S_ISREG(fs.st_mode) or fs.st_nlink != 1:
                raise PathBad('.lock', 'the file opened is not a single-link plain file')
            st = self._check('.lock', 'file')
            if (st.st_dev, st.st_ino) != (fs.st_dev, fs.st_ino):
                raise PathBad('.lock', 'the file opened is not the file checked')
            try:
                _os_lock(fd)
            except OSError:
                raise RecordStartError(LOCK_HELD)
        except BaseException:
            os.close(fd)
            raise
        self._lock_fd, self._lock_stat = fd, fs
        info = f'pid {os.getpid()} since {_now()} (for people; not used to decide anything)\n'.encode()
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, info)
        os.ftruncate(fd, len(info))

    def _scan(self):
        groups = {}

        def group(key):
            return groups.setdefault(key, {'current': None, 'history': {}})
        for e in os.scandir(self._p('cases')):
            if _is_temp(e.name):
                continue
            id_ = e.name[:-5] if e.name.endswith('.json') else None
            if id_ and CASE_ID.fullmatch(id_):
                group(id_)['current'] = f'cases/{e.name}'
            else:
                self.unknown.append(f'cases/{e.name}')
        for e in os.scandir(self._p('history')):
            if e.name == SETTINGS or _is_temp(e.name):
                continue
            if CASE_ID.fullmatch(e.name):
                self._scan_history(e.name, group(e.name))
            else:
                self.unknown.append(f'history/{e.name}')
        self._scan_history(SETTINGS, group(SETTINGS))
        if os.path.lexists(self._p('settings.json')):
            group(SETTINGS)['current'] = 'settings.json'
        for e in os.scandir(self.root):
            if e.name not in ('.lock', 'settings.json', 'cases', 'history') and not _is_temp(e.name):
                self.unknown.append(e.name)
        return sum(self._load(key, g) for key, g in groups.items())

    def _scan_history(self, key, g):
        rel = f'history/{key}'
        try:
            self._check(rel, 'dir')
        except PathBad:
            if key == SETTINGS:
                raise
            self._mark(key, 'corrupt', rel)
            return
        for e in os.scandir(self._p(rel)):
            if _is_temp(e.name):
                continue
            m = HISTORY_NAME.fullmatch(e.name)
            if m:
                g['history'][int(m.group(1))] = f'{rel}/{e.name}'
            else:
                self._mark(key, 'corrupt', f'{rel}/{e.name}')

    def _load(self, key, g):
        """Check every version file of one Case (or the settings) and build its part of the index."""
        kind = _slot(key)[0]
        idx, n = {}, 0
        cur = cur_data = None
        if g['current']:
            n += 1
            try:
                cur, cur_data = self._version(g['current'], key, kind)
            except Corrupt as e:
                self._mark(key, 'corrupt', e.rel)
        elif g['history']:
            self._mark(key, 'orphan_history', *g['history'].values())
        for rev, rel in sorted(g['history'].items()):
            n += 1
            self.hist_files += 1
            try:
                self.hist_bytes += os.lstat(self._p(rel)).st_size
            except OSError:
                pass
            try:
                doc, data = self._version(rel, key, kind, rev)
            except Corrupt as e:
                self._mark(key, 'corrupt', e.rel)
                continue
            if cur is not None and (rev > cur['rev'] or (rev == cur['rev'] and data != cur_data)):
                self._mark(key, 'corrupt', rel)      # only an identical copy of the current file may share its rev
            self._index_add(idx, key, doc, rel)
        if cur is not None:
            self._index_add(idx, key, cur, g['current'])
        self.index[key] = idx
        return n

    def _index_add(self, idx, key, doc, rel):
        seen = idx.get(doc['requestId'])
        if seen is not None and seen != (doc['rev'], doc['sha256']):
            self._mark(key, 'corrupt', rel)
        idx[doc['requestId']] = (doc['rev'], doc['sha256'])

    def close(self):
        if self._lock_fd is not None:
            try:
                _os_lock(self._lock_fd, unlock=True)
            except OSError:
                pass
            os.close(self._lock_fd)
            self._lock_fd = None

    # ---- while running ----
    def _locked(self, fn, *args):
        if self.failed:
            return 503, {'error': 'record_unavailable', 'reason': self.failed}
        if not self.lock.acquire(timeout=LOCK_WAIT):
            return 503, {'error': 'busy'}
        try:
            if self.failed:
                return 503, {'error': 'record_unavailable', 'reason': self.failed}
            try:
                self._check_fixed()
            except Fatal as e:
                return 500, {'error': 'record_path', 'reason': str(e)}
            try:
                return fn(*args)
            except Answer as e:
                return e.code, e.body
        finally:
            self.lock.release()

    def _check_fixed(self):
        """The paths the whole record depends on (§3.5 table, first row). A failure stops the record API."""
        try:
            for rel, kind in FIXED_PATHS:
                st = self._check(rel, kind)
            if (st.st_dev, st.st_ino) != (self._lock_stat.st_dev, self._lock_stat.st_ino):
                raise PathBad('.lock', 'is not the file the relay locked')
        except (PathBad, OSError) as e:
            self.failed = str(e)
            print(f'[record] stopped: {e}', file=sys.stderr)
            raise Fatal(str(e))

    def _current(self, key):
        """(doc, bytes) of the current version, (None, None) when there is none, or Answer 409 when it is unusable."""
        kind, cur_rel, hist_rel = _slot(key)
        if key in self.bad:
            raise self._corrupt(key)
        try:
            return self._version(cur_rel, key, kind)
        except FileNotFoundError:
            pass
        except Corrupt as e:
            self._mark(key, 'corrupt', e.rel)
            raise self._corrupt(key)
        if self.index.get(key) or self._history_names(key):
            self._mark(key, 'orphan_history', hist_rel)
            raise self._corrupt(key)
        return None, None

    def _history_names(self, key):
        rel = _slot(key)[2]
        if not os.path.lexists(self._p(rel)):
            return []
        try:
            self._check(rel, 'dir')
        except PathBad:
            self._mark(key, 'corrupt', rel)
            raise self._corrupt(key)
        return [n for n in os.listdir(self._p(rel)) if not _is_temp(n)]

    def _keep(self, key, rev, data):
        """§3.1-6: the current version, byte for byte, into the history (an identical copy already there is fine)."""
        hist_rel = _slot(key)[2]
        if not os.path.lexists(self._p(hist_rel)):
            os.mkdir(self._p(hist_rel))
        rel = f'{hist_rel}/{rev}.json'
        try:
            self._check(hist_rel, 'dir')
            if os.path.lexists(self._p(rel)):
                if self._read(rel) != data:
                    raise Answer(500, {'error': 'history_mismatch', 'file': rel})
                return
            self._write_atomic(rel, data)
        except PathBad as e:
            self._mark(key, 'corrupt', e.rel)
            raise self._corrupt(key)
        self.hist_files += 1
        self.hist_bytes += len(data)

    def _write(self, key, base, rid, body, body_sha):
        kind, cur_rel, _ = _slot(key)
        cur, cur_data = self._current(key)
        cur_rev = cur['rev'] if cur else 0
        seen = self.index.get(key, {}).get(rid)
        if seen is not None:
            if seen[1] != body_sha:
                raise Answer(409, {'error': 'request_id_reused', 'rev': seen[0]})
            return 200, {'rev': seen[0], 'sha256': body_sha, 'replayed': True, 'currentRev': cur_rev}
        if base != cur_rev:
            raise Answer(409, {'error': 'version_conflict', 'currentRev': cur_rev,
                               'currentSha256': cur['sha256'] if cur else None})
        _fault('before_history')
        if cur is not None:
            self._keep(key, cur_rev, cur_data)
        _fault('after_history')
        rev, saved = cur_rev + 1, _now()
        fmt = FORMATS[kind]
        doc = {'format': fmt, 'id': key, 'rev': rev, 'savedAt': saved, 'requestId': rid, 'sha256': body_sha,
               'metaSha256': meta_sha256(fmt, key, rev, saved, rid, body_sha), kind: body}
        try:
            self._write_atomic(cur_rel, json.dumps(doc, ensure_ascii=False, allow_nan=False).encode('utf-8'), final=True)
        except PathBad as e:
            self._mark(key, 'corrupt', e.rel)
            raise self._corrupt(key)
        _fault('after_replace')
        self.index.setdefault(key, {})[rid] = (rev, body_sha)
        return 200, {'rev': rev, 'sha256': body_sha, 'replayed': False}

    # ---- the API (called by studio_proxy.Handler) ----
    def write_case(self, j):
        if not isinstance(j, dict):
            raise _bad('the body must be an object')
        id_ = j.get('id')
        if not valid_case_id(id_):
            raise _bad('id must be a Case number (CASE-YYYYMMDD-NNN[-xxxxxx])')
        base, rid, case, case_sha = _check_common(j, 'case', ('id', 'baseRev', 'requestId', 'case'))
        if case.get('id') != id_:
            raise _bad('case.id differs from id')
        if created_key(case.get('createdAt')) is None:
            raise _bad('case.createdAt must be YYYY-MM-DDTHH:MM:SS(.sss)Z')

        def done():
            code, out = self._write(id_, base, rid, case, case_sha)
            return code, {'id': id_, **out}
        return self._locked(done)

    def write_settings(self, j):
        base, rid, settings, settings_sha = _check_common(j, 'settings', ('baseRev', 'requestId', 'settings'))
        return self._locked(self._write, SETTINGS, base, rid, settings, settings_sha)

    def read_case(self, id_):
        if not valid_case_id(id_):
            raise _bad('id must be a Case number')

        def read():
            cur, _ = self._current(id_)
            if cur is None:
                raise Answer(404, {'error': 'no such Case'})
            return 200, {'id': id_, 'rev': cur['rev'], 'sha256': cur['sha256'], 'case': cur['case']}
        return self._locked(read)

    def read_settings(self):
        def read():
            cur, _ = self._current(SETTINGS)
            if cur is None:
                return 200, {'rev': 0, 'sha256': None, 'settings': None}
            return 200, {'rev': cur['rev'], 'sha256': cur['sha256'], 'settings': cur['settings']}
        return self._locked(read)

    def listing(self):
        return self._locked(self._listing)

    def _listing(self):
        keys = (set(self.index) | set(self.bad)) - {SETTINGS}
        for name in os.listdir(self._p('cases')):
            if name.endswith('.json') and CASE_ID.fullmatch(name[:-5]):
                keys.add(name[:-5])
        rows = []
        for key in keys:
            if key not in self.bad:
                try:
                    cur, data = self._current(key)
                except Answer:
                    pass                      # now in self.bad
                else:
                    if cur is None:
                        continue
                    c = cur['case']
                    q = c.get('question')
                    rows.append(((created_key(c.get('createdAt')) or '', key), {
                        'id': key, 'rev': cur['rev'], 'sha256': cur['sha256'], 'bytes': len(data),
                        'createdAt': c.get('createdAt'), 'title': q.split('\n')[0][:80] if isinstance(q, str) else ''}))
                    continue
            b = self.bad[key]
            rows.append((('', key), {'id': key, 'problem': b['problem'], 'files': list(b['files'])}))
        rows.sort(key=lambda r: r[0], reverse=True)
        try:
            cur, _ = self._current(SETTINGS)
            settings = {'rev': cur['rev'], 'sha256': cur['sha256']} if cur else {'rev': 0}
        except Answer:
            b = self.bad[SETTINGS]
            settings = {'rev': None, 'problem': b['problem'], 'files': list(b['files'])}
        return 200, {'cases': [r[1] for r in rows], 'settings': settings,
                     'history': {'files': self.hist_files, 'bytes': self.hist_bytes}, 'unknownFiles': list(self.unknown)}


def open_store(root):
    """Open the record in the folder root (normally <exchange>/_record). Raises RecordStartError."""
    s = RecordStore(root)
    s.open()
    return s
