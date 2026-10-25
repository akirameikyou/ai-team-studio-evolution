"""Tests for the record API of the relay (課題 3-A). Standard library only.

Contract: ../RECORD_API_3A.md 0.6.1, §7 (R-01 .. R-26, R-13b, R-16b, R-16c, R-16d).
Written before the implementation (test first): until record_store.py exists, every test fails in setUp.

Run from operational-studio-001/proxy:   python -m unittest test_record_api -v
R-11 also reads a real copy of the Studio record when ATS_R11_SNAPSHOT names one (read only; skipped otherwise):
    set ATS_R11_SNAPSHOT=...\\exchange\\_snapshots\\ats-store-20261025T052324273Z-r464.json

Everything runs in temporary folders on random local ports; proxy/.env is never read and the running Studio
(8773 / 8787) is not touched.

The interface these tests assume (the contract leaves these names to the implementation; fixed here):
  record_store.py
    open_store(root) -> store   root is the _record folder. Creates the folders and .lock when missing, takes the
                                OS lock (§6.2), scans every version file (§3.3.1, §3.4) and writes one line to
                                sys.stderr containing "files=<number of version files>" and "seconds=<float>".
                                Raises RecordStartError when the relay must not start (§3.3.1, §3.5, §6.2).
    store.close()               releases the OS lock.
    LOCK_WAIT = 10              seconds to wait for the record lock (§6.1)
    BODY_DEADLINE = 10          absolute deadline for reading a POST body, measured with time.monotonic (§4.1)
    MAX_BODY = 2 * 1024 * 1024  (§4)
    _fault(stage)               no-op test hook, looked up on the module at each call. Stages of one write (§3.1):
                                'before_history' (after the checks, before step 6), 'after_history' (between 6
                                and 7), 'before_replace' (step 7, temporary file complete, before os.replace),
                                'after_replace' (after os.replace, before the answer).
    _before_open(path)          no-op test hook, called after a path has been checked and right before it is
                                opened (version files, settings, .lock; §3.5, R-16d).
  studio_proxy.py
    RECORD                      the open store, or None (then every /record path answers 503).
    main(argv=None)             opens EXCHANGE/_record before serving; on RecordStartError prints the reason
                                (for a held lock: 「別の中継が同じ記録を使っています」) and exits with a non-zero code.
  Error answers are JSON with "error" set to the contract's code where it names one
  (record_corrupt, version_conflict, request_id_reused, busy, history_mismatch).
"""
import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
import uuid
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import studio_proxy as sp

try:
    import record_store as rs
except ImportError:          # not written yet (test first)
    rs = None

HERE = Path(__file__).resolve().parent
MIB = 1024 * 1024
MARKER = 'OUTSIDE-MARKER-3A'          # put into files outside _record; must never appear in an answer
SAVED_AT = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$')
VERSION_KEYS = ['format', 'id', 'rev', 'savedAt', 'requestId', 'sha256', 'metaSha256']


# ---- the contract's hashes, computed independently of the implementation (§2.1) ----
def canon(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')


def sha(obj):
    return hashlib.sha256(canon(obj)).hexdigest()


def meta_sha(fmt, id_, rev, saved_at, request_id, body_sha):
    return sha([fmt, id_, rev, saved_at, request_id, body_sha])


def cid(n):
    return f'CASE-20261025-{n:03d}-t{n:05d}'


def new_rid():
    return 'r-' + uuid.uuid4().hex


def make_case(id_, question=None, created='2026-10-25T05:00:00.000Z', **extra):
    c = {'id': id_, 'question': question if question is not None else f'{id_} の問い\n二行目',
         'createdAt': created, 'sources': [], 'observations': {'GPT': {'text': '観測 🍵 \u2028 終'}},
         '未知の項目': [1, 2.5, None, True, {'深い': 'そのまま'}]}
    if created is None:
        del c['createdAt']
    c.update(extra)
    return c


def version_doc(kind, id_, rev, body, request_id, saved_at='2026-10-25T05:00:00.000Z'):
    """A well-formed version file (§2), for states the API cannot produce by itself."""
    fmt = 'ats-case-1' if kind == 'case' else 'ats-settings-1'
    h = sha(body)
    return {'format': fmt, 'id': id_, 'rev': rev, 'savedAt': saved_at, 'requestId': request_id, 'sha256': h,
            'metaSha256': meta_sha(fmt, id_, rev, saved_at, request_id, h), kind: body}


def dump(doc):
    return json.dumps(doc, ensure_ascii=False).encode('utf-8')


def load(path):
    return json.loads(Path(path).read_bytes().decode('utf-8'))


def body_key(doc):
    return 'settings' if doc.get('format') == 'ats-settings-1' else 'case'


def remark(path):
    """Turn a version file into a different, still well-formed one that carries MARKER (hashes recomputed),
    so a relay that followed a link to it would accept and serve it."""
    doc = load(path)
    k = body_key(doc)
    doc[k]['question' if k == 'case' else 'marker'] = MARKER
    h = sha(doc[k])
    doc['sha256'] = h
    doc['metaSha256'] = meta_sha(doc['format'], doc['id'], doc['rev'], doc['savedAt'], doc['requestId'], h)
    Path(path).write_bytes(dump(doc))


def tree(root):
    """{relative path: bytes} of every file below root, .lock excepted (its text is for people, §6.2)."""
    out = {}
    root = Path(root)
    if not root.exists():
        return out
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            p = Path(dirpath) / f
            if p.name != '.lock':
                out[p.relative_to(root).as_posix()] = p.read_bytes()
    return out


def free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Resp:
    def __init__(self, status, headers, body):
        self.status, self.headers, self.body = status, headers, body

    def header(self, name):
        return [v for k, v in self.headers if k == name.lower()]

    @property
    def json(self):
        try:
            return json.loads(self.body.decode('utf-8'))
        except ValueError:
            return {}

    @property
    def error(self):
        j = self.json
        return j.get('error') if isinstance(j, dict) else None

    def __repr__(self):
        return f'<{self.status} {self.body[:300]!r}>'


def read_response(sock):
    """One HTTP response from a raw socket, and whatever bytes came after it."""
    buf = b''
    while b'\r\n\r\n' not in buf:
        chunk = sock.recv(65536)
        if not chunk:
            raise ConnectionError(f'closed before the response headers: {buf[:200]!r}')
        buf += chunk
    head, rest = buf.split(b'\r\n\r\n', 1)
    lines = head.decode('iso-8859-1').split('\r\n')
    status = int(lines[0].split()[1])
    headers = [(k.strip().lower(), v.strip()) for k, v in (ln.split(':', 1) for ln in lines[1:] if ':' in ln)]
    n = next((int(v) for k, v in headers if k == 'content-length'), None)
    while n is None or len(rest) < n:
        chunk = sock.recv(65536)
        if not chunk:
            break
        rest += chunk
    if n is None:
        return Resp(status, headers, rest), b''
    return Resp(status, headers, rest[:n]), rest[n:]


def send_raw(port, method, path, headers, body=b'', timeout=20):
    s = socket.create_connection(('127.0.0.1', port), timeout=timeout)
    try:
        head = f'{method} {path} HTTP/1.1\r\n' + ''.join(f'{k}: {v}\r\n' for k, v in headers) + '\r\n'
        s.sendall(head.encode('latin-1') + body)
        return read_response(s)[0]
    finally:
        s.close()


class RecordBase(unittest.TestCase):
    def setUp(self):
        if rs is None:
            self.fail('record_store.py does not exist yet (3-A is not implemented)')
        if not hasattr(sp, 'RECORD'):
            self.fail('studio_proxy.RECORD does not exist yet (3-A is not implemented)')
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.ex = self.base / 'exchange'
        self.outside = self.base / 'outside'
        self.root = self.ex / '_record'
        self.ex.mkdir()
        self.outside.mkdir()
        self._links = []
        self._old = (sp.EXCHANGE, sp.RECORD)
        sp.EXCHANGE = self.ex
        self.store = None
        self.srv = ThreadingHTTPServer(('127.0.0.1', 0), sp.Handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.port = self.srv.server_address[1]
        self.open()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.close()
        self._unlink_all()
        sp.EXCHANGE, sp.RECORD = self._old
        self._tmp.cleanup()

    # ---- the store ----
    def open(self):
        self.store = rs.open_store(self.root)
        sp.RECORD = self.store

    def close(self):
        if self.store is not None:
            self.store.close()
        self.store = None
        sp.RECORD = None

    def restart(self):
        self.close()
        self.open()

    def reset(self):
        """A new, empty record (the server keeps running)."""
        self.close()
        self._unlink_all()
        shutil.rmtree(self.ex)
        shutil.rmtree(self.outside)
        self.ex.mkdir()
        self.outside.mkdir()
        self.open()

    # ---- HTTP ----
    def host(self):
        return ('Host', f'127.0.0.1:{self.port}')

    def get(self, path, headers=None):
        return send_raw(self.port, 'GET', path, headers if headers is not None else [self.host()])

    def post(self, path, obj=None, raw=None, headers=None):
        body = raw if raw is not None else json.dumps(obj, ensure_ascii=False).encode('utf-8')
        if headers is None:
            headers = [self.host(), ('Content-Type', 'application/json'), ('Content-Length', str(len(body)))]
        return send_raw(self.port, 'POST', path, headers, body)

    def post_case(self, case, base_rev, rid=None, id_=None):
        return self.post('/record/case', {'id': id_ if id_ is not None else case['id'], 'baseRev': base_rev,
                                          'requestId': rid or new_rid(), 'case': case})

    def post_settings(self, settings, base_rev, rid=None):
        return self.post('/record/settings', {'baseRev': base_rev, 'requestId': rid or new_rid(), 'settings': settings})

    def get_case(self, id_):
        return self.get('/record/case?id=' + id_)

    def listing(self):
        r = self.get('/record')
        self.assertEqual(r.status, 200, r)
        return r.json

    def entry(self, id_):
        hit = [c for c in self.listing()['cases'] if c['id'] == id_]
        self.assertEqual(len(hit), 1, f'{id_} is listed once')
        return hit[0]

    # ---- writing through the API ----
    def write_revs(self, id_, revs=1, created='2026-10-25T05:00:00.000Z'):
        """Write `revs` versions of one Case; returns [(requestId, case)] for rev 1, 2, ..."""
        out = []
        for k in range(revs):
            c = make_case(id_, question=f'{id_} 版{k + 1}', created=created)
            rid = new_rid()
            r = self.post_case(c, k, rid)
            self.assertEqual(r.status, 200, r)
            self.assertEqual(r.json['rev'], k + 1)
            out.append((rid, c))
        return out

    def seed(self):
        """A at rev 2 (history/A/1.json), B at rev 1, settings at rev 2 (history/_settings/1.json)."""
        self.A, self.B = cid(1), cid(2)
        self.a = self.write_revs(self.A, 2)
        self.b = self.write_revs(self.B, 1, created='2026-10-25T06:00:00.000Z')
        self.s = []
        for k in range(2):
            st, rid = {'observers': ['GPT', 'Gemini'], 'note': f'設定 {k + 1}'}, new_rid()
            r = self.post_settings(st, k, rid)
            self.assertEqual(r.status, 200, r)
            self.s.append((rid, st))

    # ---- files ----
    def case_file(self, id_):
        return self.root / 'cases' / f'{id_}.json'

    def hist_file(self, id_, rev):
        return self.root / 'history' / id_ / f'{rev}.json'

    def no_temp_files(self):
        return not [p for p in self.root.rglob('*') if p.name.endswith('.tmp')]

    def assert_case_ok(self, id_, rev):
        r = self.get_case(id_)
        self.assertEqual(r.status, 200, r)
        self.assertEqual(r.json['rev'], rev)
        self.assertNotIn(MARKER.encode(), r.body)

    def assert_case_corrupt(self, id_, base_rev, replay=None):
        r = self.get_case(id_)
        self.assertEqual((r.status, r.error), (409, 'record_corrupt'), f'{id_} read: {r}')
        self.assertNotIn(MARKER.encode(), r.body)
        r = self.post_case(make_case(id_, question='書けてはいけない'), base_rev)
        self.assertEqual((r.status, r.error), (409, 'record_corrupt'), f'{id_} write: {r}')
        if replay:
            rid, c = replay
            r = self.post_case(c, base_rev - 1, rid)
            self.assertEqual((r.status, r.error), (409, 'record_corrupt'), f'{id_} replay: {r}')
        e = self.entry(id_)
        self.assertTrue(e.get('problem'), f'{id_} is listed with a problem: {e}')
        self.assertNotIn(MARKER, json.dumps(e, ensure_ascii=False))

    def assert_connection_close(self, r):
        self.assertIn('close', [v.lower() for v in r.header('connection')], f'Connection: close on {r}')

    # ---- links ----
    def make_link(self, link, target, kind):
        """kind 'junction' (Windows, directories) or 'symlink'. Skips (with the reason) where it cannot be made."""
        link, target = Path(link), Path(target)
        if kind == 'junction':
            if os.name != 'nt':
                self.skipTest('junctions exist only on Windows')
            r = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(target)], capture_output=True)
            if r.returncode:
                self.skipTest(f'mklink /J failed ({r.returncode})')
        else:
            try:
                os.symlink(target, link, target_is_directory=target.is_dir())
            except OSError as e:
                self.skipTest(f'symbolic links cannot be made here ({type(e).__name__}: {e}); '
                              'needs Developer Mode or administrator rights on Windows')
        self._links.append(link)

    def _unlink_all(self):
        for link in reversed(self._links):
            try:
                if os.path.isdir(link):
                    os.rmdir(link)       # removes a junction / directory link only, never its target
                else:
                    os.unlink(link)
            except OSError:
                pass
        self._links = []

    def move_outside(self, item):
        """Move a file or folder of _record to the outside folder, marking every version file in the copy.
        Returns the outside path."""
        src = self.root / item if item != '_record' else self.root
        dst = self.outside / ('out-' + item.replace('/', '__'))     # never named .lock (tree() skips that name)
        if src.is_dir():
            shutil.copytree(src, dst)
            shutil.rmtree(src)
            for p in dst.rglob('*.json'):
                remark(p)
        else:
            shutil.copy2(src, dst)
            src.unlink()
            if dst.suffix == '.json':
                remark(dst)
        return dst


# =====================================================================================================
class WriteReadTests(RecordBase):
    def test_r01_new_case_is_written_read_and_listed(self):
        A = cid(1)
        c = make_case(A, question='一行目の問い' + 'あ' * 100 + '\n二行目')
        rid = new_rid()
        r = self.post_case(c, 0, rid)
        self.assertEqual(r.status, 200, r)
        self.assertEqual((r.json['id'], r.json['rev'], r.json['replayed']), (A, 1, False))
        self.assertEqual(r.json['sha256'], sha(c))
        g = self.get_case(A)
        self.assertEqual(g.status, 200, g)
        self.assertEqual(g.json, {'id': A, 'rev': 1, 'sha256': sha(c), 'case': c})
        doc = load(self.case_file(A))
        self.assertEqual(sorted(doc), sorted(VERSION_KEYS + ['case']))
        self.assertEqual((doc['format'], doc['id'], doc['rev'], doc['requestId']), ('ats-case-1', A, 1, rid))
        self.assertRegex(doc['savedAt'], SAVED_AT)
        self.assertEqual(doc['sha256'], sha(c))
        self.assertEqual(doc['metaSha256'], meta_sha('ats-case-1', A, 1, doc['savedAt'], rid, sha(c)))
        self.assertEqual(doc['case'], c)
        lst = self.listing()
        e = self.entry(A)
        self.assertEqual((e['rev'], e['sha256'], e['createdAt']), (1, sha(c), c['createdAt']))
        self.assertEqual(e['title'], ('一行目の問い' + 'あ' * 100)[:80])
        self.assertIsInstance(e['bytes'], int)
        self.assertNotIn('problem', e)
        self.assertEqual(lst['settings']['rev'], 0)
        self.assertEqual(lst['history'], {'files': 0, 'bytes': 0})
        self.assertTrue(self.no_temp_files())

    def test_r01_unknown_case_is_404(self):
        self.assertEqual(self.get_case(cid(9)).status, 404)

    def test_r02_next_version_keeps_the_previous_one_byte_for_byte(self):
        A = cid(1)
        rid1, c1 = self.write_revs(A, 1)[0]
        before = self.case_file(A).read_bytes()
        c2 = make_case(A, question='二版目')
        r = self.post_case(c2, 1)
        self.assertEqual((r.status, r.json['rev'], r.json['replayed']), (200, 2, False), r)
        self.assertEqual(self.hist_file(A, 1).read_bytes(), before)
        self.assertEqual(self.get_case(A).json['case'], c2)
        self.assertEqual(self.listing()['history'], {'files': 1, 'bytes': len(before)})
        self.assertTrue(self.no_temp_files())

    def test_r03_stale_base_rev_is_409_and_changes_nothing(self):
        A = cid(1)
        revs = self.write_revs(A, 2)
        state = tree(self.root)
        r = self.post_case(make_case(A, question='古い版から'), 1)
        self.assertEqual((r.status, r.error), (409, 'version_conflict'), r)
        self.assertEqual((r.json['currentRev'], r.json['currentSha256']), (2, sha(revs[1][1])))
        for base in (0, 3, 7):
            with self.subTest(baseRev=base):
                r = self.post_case(make_case(A, question='誤った版'), base)
                self.assertEqual((r.status, r.error), (409, 'version_conflict'), r)
        r = self.post_case(make_case(cid(2)), 5)            # a new Case must say baseRev 0
        self.assertEqual((r.status, r.error), (409, 'version_conflict'), r)
        self.assertEqual(tree(self.root), state)

    def test_r04_two_writes_from_the_same_version_one_wins(self):
        A = cid(1)
        self.write_revs(A, 1)
        for round_ in range(5):
            with self.subTest(round=round_):
                gate, out = threading.Barrier(2), []

                def write(k):
                    gate.wait()
                    out.append(self.post_case(make_case(A, question=f'同時 {round_}-{k}'), round_ + 1).status)
                ts = [threading.Thread(target=write, args=(k,)) for k in range(2)]
                for t in ts:
                    t.start()
                for t in ts:
                    t.join(30)
                self.assertEqual(sorted(out), [200, 409])
                self.assertEqual(self.get_case(A).json['rev'], round_ + 2)

    def test_r05_lost_answer_is_replayed_even_after_another_write_and_a_restart(self):
        A = cid(1)
        self.write_revs(A, 1)
        rid_a, ca = new_rid(), make_case(A, question='A の書き込み')
        self.assertEqual(self.post_case(ca, 1, rid_a).status, 200)       # the answer is "lost"
        self.assertEqual(self.post_case(make_case(A, question='B の書き込み'), 2).status, 200)
        for when in ('running', 'after restart'):
            with self.subTest(when=when):
                if when == 'after restart':
                    self.restart()
                state = tree(self.root)
                r = self.post_case(ca, 1, rid_a)
                self.assertEqual(r.status, 200, r)
                self.assertEqual((r.json['rev'], r.json['replayed'], r.json['currentRev']), (2, True, 3))
                self.assertEqual(r.json['sha256'], sha(ca))
                self.assertEqual(tree(self.root), state)

    def test_r06_same_request_id_with_other_content_is_409(self):
        A = cid(1)
        revs = self.write_revs(A, 3)
        for when in ('running', 'after restart'):
            if when == 'after restart':
                self.restart()
            for where, (rid, _c) in (('history', revs[0]), ('history', revs[1]), ('current', revs[2])):
                with self.subTest(when=when, where=where):
                    state = tree(self.root)
                    r = self.post_case(make_case(A, question='別の中身'), 3, rid)
                    self.assertEqual((r.status, r.error), (409, 'request_id_reused'), r)
                    self.assertEqual(tree(self.root), state)

    def test_r07_orphan_history_left_between_steps_6_and_7_is_normal(self):
        A = cid(1)
        rid1, c1 = self.write_revs(A, 1)[0]
        v1 = self.case_file(A).read_bytes()
        self.close()
        self.hist_file(A, 1).parent.mkdir(parents=True)
        self.hist_file(A, 1).write_bytes(v1)                # step 6 done, step 7 never happened
        self.open()
        self.assertNotIn('problem', self.entry(A))
        self.assert_case_ok(A, 1)
        r = self.post_case(c1, 0, rid1)                     # the first write, sent again
        self.assertEqual((r.status, r.json['rev'], r.json['replayed']), (200, 1, True), r)
        c2, rid2 = make_case(A, question='二版目'), new_rid()
        r = self.post_case(c2, 1, rid2)
        self.assertEqual((r.status, r.json['rev'], r.json['replayed']), (200, 2, False), r)
        self.assertEqual(self.hist_file(A, 1).read_bytes(), v1)
        self.assertEqual(self.get_case(A).json['case'], c2)

    def test_r07_a_different_history_copy_found_while_writing_is_500_history_mismatch(self):
        """§3.1-6: a history file already there for the current rev must be byte-identical; changed while the
        relay runs (the start-up check cannot see it), the write stops and changes nothing."""
        A = cid(1)
        rid1, _c1 = self.write_revs(A, 1)[0]
        v1 = self.case_file(A).read_bytes()
        other = version_doc('case', A, 1, make_case(A, question='別の中身の履歴'), rid1)
        variants = {
            'other content': dump(other),
            'same content, other bytes': json.dumps(load(self.case_file(A)), ensure_ascii=True, indent=1).encode(),
        }
        self.hist_file(A, 1).parent.mkdir(parents=True)
        for name, data in variants.items():
            with self.subTest(name):
                self.assertNotEqual(data, v1)
                self.hist_file(A, 1).write_bytes(data)
                state = tree(self.root)
                c2, rid2 = make_case(A, question='二版目'), new_rid()
                for attempt in ('first', 'resent'):
                    r = self.post_case(c2, 1, rid2)
                    self.assertEqual((r.status, r.error), (500, 'history_mismatch'), f'{attempt}: {r}')
                    self.assertEqual(tree(self.root), state, 'neither the current file nor the history changed')
                    self.assertTrue(self.no_temp_files())
                self.assert_case_ok(A, 1)


# =====================================================================================================
class RejectTests(RecordBase):
    def test_r08_bad_requests_are_refused_and_nothing_is_written(self):
        A, B = cid(1), cid(2)
        self.write_revs(A, 1)
        good = {'id': B, 'baseRev': 0, 'requestId': new_rid(), 'case': make_case(B)}

        def variant(**changes):
            body = json.loads(json.dumps(good))
            for k, v in changes.items():
                if k.startswith('case.'):
                    if v is KeyError:
                        del body['case'][k[5:]]
                    else:
                        body['case'][k[5:]] = v
                elif v is KeyError:
                    del body[k]
                else:
                    body[k] = v
            return json.dumps(body, ensure_ascii=False).encode('utf-8')
        good_text = json.dumps(good, ensure_ascii=False)
        bodies = {
            'broken JSON': (b'{"id": "' + B.encode() + b'", "baseRev": 0,', 400),
            'NaN': (good_text.replace('"baseRev": 0', '"baseRev": 0, "x": NaN').encode('utf-8'), 400),
            'NaN in case': (good_text.replace('"sources": []', '"sources": [NaN]').encode('utf-8'), 400),
            'Infinity': (good_text.replace('"sources": []', '"sources": [Infinity]').encode('utf-8'), 400),
            '-Infinity': (good_text.replace('"sources": []', '"sources": [-Infinity]').encode('utf-8'), 400),
            'duplicate key': (good_text.replace('{"id": ', '{"id": "' + B + '", "id": ', 1).encode('utf-8'), 400),
            'duplicate key in case': (good_text.replace('"sources": []', '"sources": [], "sources": []').encode('utf-8'), 400),
            'lone surrogate': (good_text.replace('"sources": []', '"sources": ["\\ud800"]').encode('utf-8'), 400),
            'id differs from case.id': (variant(id=cid(3)), 400),
            'id missing': (variant(id=KeyError), 400),
            'createdAt missing': (variant(**{'case.createdAt': KeyError}), 400),
            'createdAt without zone': (variant(**{'case.createdAt': '2026-10-25T05:00:00.000'}), 400),
            'createdAt as date only': (variant(**{'case.createdAt': '2026-10-25'}), 400),
            'createdAt as number': (variant(**{'case.createdAt': 1761368400000}), 400),
            'case is a list': (variant(case=[1, 2]), 400),
            'case missing': (variant(case=KeyError), 400),
            'baseRev -1': (variant(baseRev=-1), 400),
            'baseRev true': (variant(baseRev=True), 400),
            'baseRev "0"': (variant(baseRev='0'), 400),
            'baseRev 0.5': (variant(baseRev=0.5), 400),
            'baseRev missing': (variant(baseRev=KeyError), 400),
            'requestId too short': (variant(requestId='short'), 400),
            'requestId with a space': (variant(requestId='bad request id'), 400),
            'requestId 65 chars': (variant(requestId='a' * 65), 400),
            'requestId missing': (variant(requestId=KeyError), 400),
            'over 2 MiB': (variant(**{'case.question': 'x' * (2 * MIB + 10)}), 413),
            'body is not an object': (b'[1, 2, 3]', 400),
        }
        for name, (body, want) in bodies.items():
            with self.subTest(name):
                state = tree(self.root)
                r = self.post('/record/case', raw=body)
                self.assertEqual(r.status, want, r)
                self.assertEqual(tree(self.root), state)
                self.assertTrue(self.no_temp_files())
        with self.subTest('Content-Type text/plain'):
            body = json.dumps(good).encode()
            r = self.post('/record/case', raw=body, headers=[
                self.host(), ('Content-Type', 'text/plain'), ('Content-Length', str(len(body)))])
            self.assertEqual(r.status, 415, r)
        self.assertEqual(self.get_case(B).status, 404)
        self.assertEqual([c['id'] for c in self.listing()['cases']], [A])

    def test_r16_case_ids_that_could_be_paths_are_refused(self):
        self.write_revs(cid(1), 1)
        state = tree(self.ex)
        bad = ['..', '../CASE-20261025-001-t00001', 'CASE-20261025-001-t00001/..', 'CASE-20261025-001-t00001\\..',
               '..\\..\\x', 'C:\\x', 'C:x', '/etc/x', 'CASE-20261025-001-ABCDEF', 'CASE-20261025-001-t00001.json',
               'CASE-20261025-001-t00001 ', 'case-20261025-001', '_settings', '']
        for b in bad:
            with self.subTest(id=b):
                r = self.post_case(make_case(b), 0)
                self.assertEqual(r.status, 400, r)
                r = self.get('/record/case?id=' + urllib.parse.quote(b, safe=''))
                self.assertEqual(r.status, 400, r)
        self.assertEqual(tree(self.ex), state)


# =====================================================================================================
class FailureTests(RecordBase):
    def test_r09_failure_at_each_step_answers_500_and_a_resend_completes(self):
        import errno
        real_replace, real_fsync = os.replace, os.fsync
        full = OSError(errno.ENOSPC, 'No space left on device (test)')

        def replace_failing(part):
            def fake(src, dst, *a, **k):
                if Path(dst).parent.name == part or Path(dst).parent.parent.name == part:
                    raise full
                return real_replace(src, dst, *a, **k)
            return fake

        def fsync_failing(nth):
            calls = []

            def fake(fd):
                calls.append(fd)
                if len(calls) == nth:
                    raise full
                return real_fsync(fd)
            return fake
        scenarios = [
            ('history file cannot be put in place (disk full)', mock.patch.object(os, 'replace', replace_failing('history'))),
            ('current file cannot be put in place (disk full)', mock.patch.object(os, 'replace', replace_failing('cases'))),
            ('fsync of the history file fails', mock.patch.object(os, 'fsync', fsync_failing(1))),
            ('fsync of the current file fails', mock.patch.object(os, 'fsync', fsync_failing(2))),
            ('replace keeps being refused (file in use)',
             mock.patch.object(os, 'replace', side_effect=PermissionError('in use (test)'))),
        ]
        for n, (name, patch) in enumerate(scenarios, start=1):
            with self.subTest(name):
                A = cid(n)
                self.write_revs(A, 1)
                before = self.case_file(A).read_bytes()
                c2, rid = make_case(A, question='失敗の後で送り直す'), new_rid()
                with mock.patch.object(sp, 'REPLACE_WAIT', 0), patch:
                    r = self.post_case(c2, 1, rid)
                self.assertEqual(r.status, 500, r)
                self.assertEqual(self.case_file(A).read_bytes(), before)
                self.assertTrue(self.no_temp_files(), 'no temporary file is left')
                self.assert_case_ok(A, 1)
                r = self.post_case(c2, 1, rid)
                self.assertEqual((r.status, r.json.get('rev'), r.json.get('replayed')), (200, 2, False), r)
                self.assertEqual(self.hist_file(A, 1).read_bytes(), before)
                self.assertEqual(self.get_case(A).json['case'], c2)

    def test_r10_corrupt_current_files_are_found_and_not_written(self):
        ids = {k: cid(n) for n, k in enumerate(['broken', 'sha', 'idcase', 'rev0', 'revstr', 'healthy', 'later'], 1)}
        for id_ in ids.values():
            self.write_revs(id_, 1)
        f = self.case_file
        f(ids['broken']).write_bytes(f(ids['broken']).read_bytes()[:-40])
        d = load(f(ids['sha']))
        d['case']['question'] = '書き換えた（sha256 はそのまま）'
        f(ids['sha']).write_bytes(dump(d))
        d = load(f(ids['idcase']))
        d['case']['id'] = cid(99)                     # case.id differs from id and the file name
        d['sha256'] = sha(d['case'])
        d['metaSha256'] = meta_sha(d['format'], d['id'], d['rev'], d['savedAt'], d['requestId'], d['sha256'])
        f(ids['idcase']).write_bytes(dump(d))
        for key, rev in (('rev0', 0), ('revstr', '1')):
            d = load(f(ids[key]))
            d['rev'] = rev
            d['metaSha256'] = meta_sha(d['format'], d['id'], rev, d['savedAt'], d['requestId'], d['sha256'])
            f(ids[key]).write_bytes(dump(d))
        renamed = cid(50)                             # a valid file of another Case under this Case's name
        shutil.copy2(f(ids['healthy']), f(renamed))
        self.restart()
        for key in ('broken', 'sha', 'idcase', 'rev0', 'revstr'):
            with self.subTest(key):
                self.assert_case_corrupt(ids[key], 1)
                self.assertIn(f'{ids[key]}.json', json.dumps(self.entry(ids[key])))
        with self.subTest('file name differs from id'):
            self.assert_case_corrupt(renamed, 1)
        with self.subTest('healthy Case is still usable'):
            self.assert_case_ok(ids['healthy'], 1)
            self.assertEqual(self.post_case(make_case(ids['healthy'], question='続き'), 1).status, 200)
        with self.subTest('found when read, without a restart'):
            d = load(f(ids['later']))
            d['case']['question'] = '動いている間に壊れた'
            f(ids['later']).write_bytes(dump(d))
            self.assert_case_corrupt(ids['later'], 1)

    def test_r10_corrupt_settings_block_only_the_settings(self):
        self.seed()
        p = self.root / 'settings.json'
        p.write_bytes(p.read_bytes()[:-30])
        self.restart()
        r = self.get('/record/settings')
        self.assertEqual((r.status, r.error), (409, 'record_corrupt'), r)
        r = self.post_settings({'note': '書けてはいけない'}, 2)
        self.assertEqual((r.status, r.error), (409, 'record_corrupt'), r)
        self.assertTrue(self.listing()['settings'].get('problem'))
        self.assert_case_ok(self.B, 1)
        self.assertEqual(self.post_case(make_case(self.B, question='設定が壊れていても書ける'), 1).status, 200)

    def test_r17_a_corrupt_history_version_makes_its_case_corrupt(self):
        ids = {k: cid(n) for n, k in enumerate(['broken', 'sha', 'meta', 'revname', 'healthy'], 1)}
        firsts = {k: self.write_revs(i, 2)[0] for k, i in ids.items()}
        h = self.hist_file
        h(ids['broken'], 1).write_bytes(h(ids['broken'], 1).read_bytes()[:-25])
        d = load(h(ids['sha'], 1))
        d['case']['question'] = '履歴を書き換えた'
        h(ids['sha'], 1).write_bytes(dump(d))
        d = load(h(ids['meta'], 1))
        d['metaSha256'] = '0' * 64
        h(ids['meta'], 1).write_bytes(dump(d))
        d = load(h(ids['revname'], 1))
        d['rev'] = 3
        d['metaSha256'] = meta_sha(d['format'], d['id'], 3, d['savedAt'], d['requestId'], d['sha256'])
        h(ids['revname'], 1).write_bytes(dump(d))
        self.restart()
        for key in ('broken', 'sha', 'meta', 'revname'):
            with self.subTest(key):
                self.assert_case_corrupt(ids[key], 2, replay=firsts[key])
                self.assertIn('1.json', json.dumps(self.entry(ids[key])))
        self.assert_case_ok(ids['healthy'], 2)

    def test_r18_changed_metadata_extra_or_missing_fields_make_a_version_corrupt(self):
        changes = {
            'format': lambda d: d.update(format='ats-case-2'),
            'id': lambda d: d.update(id=cid(999)),
            'rev': lambda d: d.update(rev=d['rev'] + 1),
            'savedAt': lambda d: d.update(savedAt='2001-01-01T00:00:00.000Z'),
            'requestId': lambda d: d.update(requestId='zzzzzzzz-changed'),
            'sha256': lambda d: d.update(sha256='0' * 64),
            'extra field': lambda d: d.update(note='余分'),
            'savedAt missing': lambda d: d.pop('savedAt'),
            'metaSha256 missing': lambda d: d.pop('metaSha256'),
        }
        made, n = [], 100
        for where in ('current', 'history'):
            for name, change in changes.items():
                n += 1
                id_ = cid(n)
                self.write_revs(id_, 2)
                p = self.case_file(id_) if where == 'current' else self.hist_file(id_, 1)
                d = load(p)
                change(d)
                p.write_bytes(dump(d))
                made.append((where, name, id_))
        self.restart()
        for where, name, id_ in made:
            with self.subTest(where=where, change=name):
                self.assert_case_corrupt(id_, 2)

    def test_r19_settings_replay_and_reuse(self):
        r1, s1 = new_rid(), {'note': '設定 A'}
        self.assertEqual(self.post_settings(s1, 0, r1).status, 200)
        self.assertEqual(self.post_settings({'note': '設定 B'}, 1).status, 200)
        for when in ('running', 'after restart'):
            with self.subTest(when=when):
                if when == 'after restart':
                    self.restart()
                state = tree(self.root)
                r = self.post_settings(s1, 0, r1)
                self.assertEqual((r.status, r.json.get('rev'), r.json.get('replayed'), r.json.get('currentRev')),
                                 (200, 1, True, 2), r)
                r = self.post_settings({'note': '別の中身'}, 2, r1)
                self.assertEqual((r.status, r.error), (409, 'request_id_reused'), r)
                self.assertEqual(tree(self.root), state)
        with self.subTest('request ids are looked up per Case / per settings'):
            c = make_case(cid(1))
            self.assertEqual(self.post_case(c, 0, r1).status, 200)
            r = self.get('/record/settings')
            self.assertEqual((r.status, r.json['rev'], r.json['settings']), (200, 2, {'note': '設定 B'}))
        with self.subTest('stale settings baseRev'):
            r = self.post_settings({'note': '古い'}, 1)
            self.assertEqual((r.status, r.error), (409, 'version_conflict'), r)


# =====================================================================================================
class ListTests(RecordBase):
    def test_r11_largest_case_round_trips(self):
        A = cid(1)
        c = make_case(A, question='観測' * 69000 + '\n終 \x00 \t 🍵', note={'深さ': [[[['四段']]]]})
        self.assertGreater(len(canon(c)), 397 * 1024)
        r = self.post_case(c, 0)
        self.assertEqual((r.status, r.json['sha256']), (200, sha(c)), r)
        for when in ('running', 'after restart'):
            with self.subTest(when=when):
                if when == 'after restart':
                    self.restart()
                g = self.get_case(A)
                self.assertEqual((g.json['sha256'], g.json['case']), (sha(c), c))

    def test_r11_real_record_round_trips_in_the_same_order(self):
        path = os.environ.get('ATS_R11_SNAPSHOT')
        if not path:
            self.skipTest('set ATS_R11_SNAPSHOT to a copy of the Studio record (read only)')
        store = json.loads(Path(path).read_bytes().decode('utf-8'))
        order = [i for i in store['order'] if i in store['cases']]
        self.assertEqual(sorted(order), sorted(store['cases']))
        for id_ in order:
            r = self.post_case(store['cases'][id_], 0)
            self.assertEqual(r.status, 200, f'{id_}: {r}')
        for when in ('running', 'after restart'):
            with self.subTest(when=when, cases=len(order)):
                if when == 'after restart':
                    self.restart()
                for id_ in order:
                    g = self.get_case(id_)
                    self.assertEqual(g.json['sha256'], sha(store['cases'][id_]), id_)
                    self.assertEqual(g.json['case'], store['cases'][id_], id_)
                self.assertEqual([c['id'] for c in self.listing()['cases']], order)

    def test_r12_order_is_newest_created_first_then_case_number(self):
        same = '2026-10-25T05:00:00.000Z'
        rows = [(cid(1), same), (cid(3), same), (cid(2), same), (cid(4), '2026-10-25T04:59:59.999Z'),
                (cid(5), '2026-10-25T05:00:00.500Z'), (cid(6), '2026-10-25T05:00:01Z'), (cid(7), '2026-10-24T23:00:00Z')]
        for id_, created in rows:
            self.assertEqual(self.post_case(make_case(id_, created=created), 0).status, 200)
        want = [cid(6), cid(5), cid(3), cid(2), cid(1), cid(4), cid(7)]
        self.assertEqual([c['id'] for c in self.listing()['cases']], want)
        self.restart()
        self.assertEqual([c['id'] for c in self.listing()['cases']], want)


# =====================================================================================================
class GateTests(RecordBase):
    def requests(self, headers_extra, host):
        body = json.dumps({'id': cid(1), 'baseRev': 0, 'requestId': new_rid(), 'case': make_case(cid(1))}).encode()
        hh = [('Host', h) for h in host]
        return [
            ('GET /record', lambda: send_raw(self.port, 'GET', '/record', hh + headers_extra)),
            ('GET /record/settings', lambda: send_raw(self.port, 'GET', '/record/settings', hh + headers_extra)),
            ('OPTIONS /record/case', lambda: send_raw(self.port, 'OPTIONS', '/record/case', hh + headers_extra + [
                ('Access-Control-Request-Method', 'POST'), ('Access-Control-Request-Headers', 'content-type')])),
            ('POST /record/case', lambda: send_raw(self.port, 'POST', '/record/case', hh + headers_extra + [
                ('Content-Type', 'application/json'), ('Content-Length', str(len(body)))], body)),
        ]

    def test_r13_other_origins_and_hosts_are_refused_without_cors_headers(self):
        good_host = [f'127.0.0.1:{self.port}']
        deny = [
            ('GitHub Pages origin', [('Origin', 'https://akirameikyou.github.io')], good_host),
            ('null origin', [('Origin', 'null')], good_host),
            ('two origins', [('Origin', 'http://localhost:8783'), ('Origin', 'http://localhost:8783')], good_host),
            ('look-alike origin', [('Origin', 'http://localhost.example.com')], good_host),
            ('origin with a path', [('Origin', 'http://127.0.0.1:8783/')], good_host),
            ('other host', [], [f'example.com:{self.port}']),
            ('no host', [], []),
            ('two hosts', [], good_host * 2),
            ('other port', [], [f'127.0.0.1:{self.port + 1 if self.port < 65535 else 1}']),
            ('no port', [], ['127.0.0.1']),
            ('localhost without port', [], ['localhost']),
        ]
        for name, extra, host in deny:
            for what, call in self.requests(extra, host):
                with self.subTest(name, request=what):
                    state = tree(self.root)
                    r = call()
                    self.assertEqual(r.status, 403, r)
                    self.assertEqual([k for k, _v in r.headers if k.startswith('access-control-')], [])
                    self.assertEqual(tree(self.root), state)

    def test_r13b_local_hosts_and_origins_pass(self):
        n = 0
        for host in (f'127.0.0.1:{self.port}', f'localhost:{self.port}', f'LOCALHOST:{self.port}'):
            for origin in (None, 'http://localhost:8783', 'http://127.0.0.1:8783'):
                extra = [('Origin', origin)] if origin else []
                with self.subTest(host=host, origin=origin):
                    for path in ('/record', '/record/settings'):
                        r = send_raw(self.port, 'GET', path, [('Host', host)] + extra)
                        self.assertIn(r.status, (200, 404) if path == '/record/settings' else (200,), r)
                    r = send_raw(self.port, 'OPTIONS', '/record/case', [('Host', host)] + extra + [
                        ('Access-Control-Request-Method', 'POST')])
                    self.assertLess(r.status, 300, r)
                    if origin:
                        self.assertEqual(r.header('access-control-allow-origin'), [origin])
                    n += 1
                    c = make_case(cid(n))
                    body = json.dumps({'id': c['id'], 'baseRev': 0, 'requestId': new_rid(), 'case': c}).encode()
                    r = send_raw(self.port, 'POST', '/record/case', [('Host', host)] + extra + [
                        ('Content-Type', 'application/json'), ('Content-Length', str(len(body)))], body)
                    self.assertEqual(r.status, 200, r)
                    if origin:
                        self.assertEqual(r.header('access-control-allow-origin'), [origin])


# =====================================================================================================
class BodyTests(RecordBase):
    def body_for(self, n=1):
        c = make_case(cid(n))
        return json.dumps({'id': c['id'], 'baseRev': 0, 'requestId': new_rid(), 'case': c}).encode('utf-8')

    def test_r20_body_framing_is_strict(self):
        body = self.body_for()
        H, CT = self.host(), ('Content-Type', 'application/json')
        CL = ('Content-Length', str(len(body)))
        cases = [
            ('Transfer-Encoding chunked', [H, CT, ('Transfer-Encoding', 'chunked')],
             f'{len(body):x}\r\n'.encode() + body + b'\r\n0\r\n\r\n', 400),
            ('Transfer-Encoding with Content-Length', [H, CT, CL, ('Transfer-Encoding', 'identity')], body, 400),
            ('no Content-Length', [H, CT], b'', 411),
            ('two Content-Length', [H, CT, CL, CL], body, 400),
            ('Content-Length -1', [H, CT, ('Content-Length', '-1')], body, 400),
            ('Content-Length +5', [H, CT, ('Content-Length', '+5')], body, 400),
            ('Content-Length abc', [H, CT, ('Content-Length', 'abc')], body, 400),
            ('Content-Length 1e3', [H, CT, ('Content-Length', '1e3')], body, 400),
            ('Content-Length 9 digits', [H, CT, ('Content-Length', '000000100')], body, 400),
            ('Content-Length over 2 MiB', [H, CT, ('Content-Length', str(2 * MIB + 1))], b'', 413),
            ('no Content-Type', [H, CL], body, 415),
            ('two Content-Type', [H, CT, CT, CL], body, 415),
            ('text/plain', [H, ('Content-Type', 'text/plain'), CL], body, 415),
            ('charset latin-1', [H, ('Content-Type', 'application/json; charset=latin-1'), CL], body, 415),
            ('other parameter', [H, ('Content-Type', 'application/json; foo=bar'), CL], body, 415),
            ('BOM', [H, CT, ('Content-Length', str(len(body) + 3))], b'\xef\xbb\xbf' + body, 400),
        ]
        for name, headers, payload, want in cases:
            with self.subTest(name):
                state = tree(self.root)
                t0 = time.monotonic()
                r = send_raw(self.port, 'POST', '/record/case', headers, payload)
                self.assertEqual(r.status, want, r)
                self.assertLess(time.monotonic() - t0, 5, 'answered without waiting for a body')
                self.assert_connection_close(r)
                self.assertEqual(tree(self.root), state)
        with self.subTest('body shorter than Content-Length is 408'), mock.patch.object(rs, 'BODY_DEADLINE', 1.0):
            t0 = time.monotonic()
            r = send_raw(self.port, 'POST', '/record/case', [H, CT, ('Content-Length', str(len(body) + 50))], body)
            self.assertEqual(r.status, 408, r)
            self.assertLess(time.monotonic() - t0, 4)
            self.assertEqual(self.get_case(cid(1)).status, 404)
        with self.subTest('charset utf-8 in any case is accepted'):
            r = send_raw(self.port, 'POST', '/record/case',
                         [H, ('Content-Type', 'Application/JSON; charset=UTF-8'), CL], body)
            self.assertEqual(r.status, 200, r)
            self.assert_connection_close(r)

    def slow_send(self, frozen_clock):
        body = self.body_for()
        stop = threading.Event()
        # the patches are in place before the relay can start reading the body
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(rs, 'BODY_DEADLINE', 1.0))
            if frozen_clock:      # a deadline measured with the wall clock would never pass
                stack.enter_context(mock.patch('time.time', return_value=1_000_000_000.0))
            s = stack.enter_context(socket.create_connection(('127.0.0.1', self.port), timeout=20))
            s.sendall(f'POST /record/case HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\nContent-Type: application/json'
                      f'\r\nContent-Length: {len(body)}\r\n\r\n'.encode())

            def drip():
                for b in body:
                    if stop.wait(0.2):
                        return
                    try:
                        s.sendall(bytes([b]))
                    except OSError:
                        return
            t = threading.Thread(target=drip, daemon=True)
            t0 = time.monotonic()
            t.start()
            r = read_response(s)[0]
            elapsed = time.monotonic() - t0
            stop.set()
            t.join(5)
        return r, elapsed

    def test_r25_a_slow_sender_gets_408_after_the_deadline(self):
        for frozen in (False, True):
            with self.subTest(wall_clock_frozen=frozen):
                r, elapsed = self.slow_send(frozen)
                self.assertEqual(r.status, 408, r)
                self.assertGreater(elapsed, 0.8)
                self.assertLess(elapsed, 3.0, 'the deadline is absolute and measured with time.monotonic')
                self.assertEqual(self.get_case(cid(1)).status, 404)

    def test_r26_post_answers_close_the_connection_and_extra_bytes_are_not_a_request(self):
        A, B = cid(1), cid(2)
        first = self.body_for(1)
        cb = make_case(B)
        second = json.dumps({'id': B, 'baseRev': 0, 'requestId': new_rid(), 'case': cb}).encode()
        smuggled = (f'POST /record/case HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\nContent-Type: application/json\r\n'
                    f'Content-Length: {len(second)}\r\n\r\n').encode() + second
        for name, payload, want in (('success', first, 200), ('failure', self.body_for(1), 409)):
            with self.subTest(name):
                s = socket.create_connection(('127.0.0.1', self.port), timeout=10)
                try:
                    s.sendall(f'POST /record/case HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\nContent-Type: '
                              f'application/json\r\nContent-Length: {len(payload)}\r\n\r\n'.encode() + payload + smuggled)
                    r, rest = read_response(s)
                    self.assertEqual(r.status, want, r)
                    self.assert_connection_close(r)
                    t0 = time.monotonic()
                    while True:
                        chunk = s.recv(65536)
                        if not chunk:
                            break
                        rest += chunk
                    self.assertEqual(rest, b'', 'nothing after the first answer')
                    self.assertLess(time.monotonic() - t0, 5, 'the relay closed the connection')
                finally:
                    s.close()
        self.assertEqual(self.get_case(B).status, 404, 'the smuggled request was not processed')
        self.assertEqual(self.get_case(A).json['rev'], 1)


# =====================================================================================================
class LockTests(RecordBase):
    def stage_hook(self, stage, action):
        real = rs._fault

        def hook(s):
            if s == stage:
                action()
            return real(s)
        return mock.patch.object(rs, '_fault', hook)

    def test_r15_a_read_during_a_write_waits_and_sees_the_written_state(self):
        A = cid(1)
        self.write_revs(A, 1)
        inside = threading.Event()
        out = {}

        def slow():
            inside.set()
            time.sleep(1.0)
        with self.stage_hook('after_history', slow):
            t = threading.Thread(target=lambda: out.update(w=self.post_case(make_case(A, question='二版目'), 1)))
            t.start()
            self.assertTrue(inside.wait(10))
            t0 = time.monotonic()
            g = self.get_case(A)
            waited = time.monotonic() - t0
            t.join(10)
        self.assertEqual(out['w'].status, 200)
        self.assertEqual((g.status, g.json['rev']), (200, 2), 'not the state between history and current')
        self.assertGreater(waited, 0.5)

    def test_r15_waiting_longer_than_the_limit_is_503(self):
        A = cid(1)
        self.write_revs(A, 1)
        inside = threading.Event()

        def slow():
            inside.set()
            time.sleep(2.0)
        with mock.patch.object(rs, 'LOCK_WAIT', 0.5), self.stage_hook('after_history', slow):
            t = threading.Thread(target=lambda: self.post_case(make_case(A, question='二版目'), 1))
            t.start()
            self.assertTrue(inside.wait(10))
            t0 = time.monotonic()
            for path in ('/record', '/record/case?id=' + A):
                g = self.get(path)
                self.assertEqual((g.status, g.error), (503, 'busy'), g)
            self.assertLess(time.monotonic() - t0, 1.9)
            t.join(10)

    def test_r15_an_exception_inside_releases_the_lock(self):
        A = cid(1)
        self.write_revs(A, 1)

        def boom():
            raise RuntimeError('test failure inside the lock')
        with mock.patch.object(rs, 'LOCK_WAIT', 1.0):
            with self.stage_hook('before_replace', boom):
                r = self.post_case(make_case(A, question='二版目'), 1)
                self.assertEqual(r.status, 500, r)
            t0 = time.monotonic()
            self.assertEqual(self.get('/record').status, 200)
            self.assertLess(time.monotonic() - t0, 0.9)
            self.assertEqual(self.post_case(make_case(A, question='二版目'), 1).status, 200)
        self.assertTrue(self.no_temp_files())


# =====================================================================================================
class StartupTests(RecordBase):
    def test_r22_index_accepts_only_an_identical_orphan_copy_and_logs_the_scan(self):
        A, B = cid(1), cid(2)
        self.write_revs(A, 2)
        self.write_revs(B, 2)
        self.close()
        cur = load(self.case_file(A))                     # same requestId in an orphan copy: same rev, same sha
        (self.hist_file(A, 2)).write_bytes(self.case_file(A).read_bytes())
        other = version_doc('case', B, 1, make_case(B, question='別の版'), load(self.case_file(B))['requestId'])
        self.hist_file(B, 1).write_bytes(dump(other))   # same requestId as the current one, other rev and sha
        err = io.StringIO()
        with mock.patch.object(sys, 'stderr', err):
            self.open()
        self.assertNotIn('problem', self.entry(A))
        self.assertEqual(self.get_case(A).json['sha256'], cur['sha256'])
        self.assert_case_corrupt(B, 2)
        log = err.getvalue()
        self.assertRegex(log, r'files=5\b', log)        # cases A, B; history A/1, A/2, B/1
        self.assertRegex(log, r'seconds=\d', log)

    def test_r22_a_write_is_added_to_the_index(self):
        A = cid(1)
        rid, c = self.write_revs(A, 1)[0]
        r = self.post_case(c, 0, rid)                     # found without a restart
        self.assertEqual((r.status, r.json['replayed']), (200, True), r)
        r = self.post_case(make_case(A, question='別'), 1, rid)
        self.assertEqual((r.status, r.error), (409, 'request_id_reused'), r)

    def test_r23_history_without_a_current_file(self):
        A, B = cid(1), cid(2)
        first = self.write_revs(A, 2)[0]
        self.write_revs(B, 1)
        self.close()
        self.case_file(A).unlink()
        self.open()
        e = self.entry(A)
        self.assertEqual(e.get('problem'), 'orphan_history', e)
        r = self.get_case(A)
        self.assertEqual((r.status, r.error), (409, 'record_corrupt'), r)
        r = self.post_case(make_case(A, question='作り直し'), 0)
        self.assertEqual((r.status, r.error), (409, 'record_corrupt'), r)
        r = self.post_case(first[1], 0, first[0])
        self.assertEqual((r.status, r.error), (409, 'record_corrupt'), r)
        self.assert_case_ok(B, 1)
        self.assertEqual(self.post_case(make_case(B, question='続き'), 1).status, 200)

    def test_r24_startup_sorts_out_what_it_finds_and_keeps_going(self):
        self.seed()
        C, D = cid(3), cid(4)
        self.write_revs(C, 2)
        self.write_revs(D, 2)
        self.close()
        (self.root / 'cases' / 'notes.txt').write_bytes(b'memo')
        (self.root / 'cases' / f'.{self.A}.json.123.tmp').write_bytes(b'half')
        (self.hist_file(C, 1).parent / 'x.json').write_bytes(b'{}')
        (self.root / 'history' / 'not-a-case').mkdir()
        (self.root / 'history' / 'not-a-case' / '1.json').write_bytes(b'{}')
        (self.hist_file(D, 1).parent / '.1.json.99.tmp').write_bytes(b'half')
        p = self.root / 'settings.json'
        p.write_bytes(b'{')
        self.open()                                       # must not raise
        lst = self.listing()
        unknown = json.dumps(lst.get('unknownFiles'), ensure_ascii=False)
        self.assertIn('notes.txt', unknown)
        self.assertIn('not-a-case', unknown)
        self.assertNotIn('.tmp', unknown)
        self.assert_case_corrupt(C, 2)
        for id_, rev in ((self.A, 2), (self.B, 1), (D, 2)):
            with self.subTest(id_):
                self.assert_case_ok(id_, rev)
                self.assertNotIn('problem', self.entry(id_))
        self.assertTrue(lst['settings'].get('problem'))
        self.assertEqual(self.post_case(make_case(D, question='書ける'), 2).status, 200)


# =====================================================================================================
CHILD = r'''
import os, sys
sys.path.insert(0, sys.argv[1])
from pathlib import Path
import studio_proxy as sp, record_store as rs
sp.EXCHANGE = Path(sys.argv[2])
sp.load_env = lambda: None
stage = sys.argv[4]
if stage != '-':
    real = rs._fault
    def _fault(s):
        if s == stage:
            os._exit(9)
        return real(s)
    rs._fault = _fault
sp.main(['--port', sys.argv[3]])
'''


class ProcessTests(RecordBase):
    """Relays in their own processes (R-14, R-21). They use their own exchange folders."""

    def spawn(self, ex, stage='-'):
        port = free_port()
        log = open(self.base / f'child-{port}.log', 'w+b')
        p = subprocess.Popen([sys.executable, '-X', 'utf8', '-c', CHILD, str(HERE), str(ex), str(port), stage],
                             stdout=log, stderr=subprocess.STDOUT, cwd=str(self.base))
        self._children.append((p, log))
        return p, port, log

    def setUp(self):
        super().setUp()
        self._children = []

    def tearDown(self):
        for p, log in self._children:
            if p.poll() is None:
                p.kill()
                p.wait(10)
            log.close()
        super().tearDown()

    def output(self, log):
        log.flush()
        log.seek(0)
        return log.read().decode('utf-8', 'replace')

    def wait_ready(self, p, port, log, timeout=20):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if p.poll() is not None:
                self.fail(f'relay exited ({p.returncode}): {self.output(log)}')
            try:
                if send_raw(port, 'GET', '/record', [('Host', f'127.0.0.1:{port}')], timeout=2).status == 200:
                    return
            except OSError:
                pass
            time.sleep(0.1)
        self.fail(f'relay did not start: {self.output(log)}')

    def call(self, port, method, path, obj=None):
        body = json.dumps(obj, ensure_ascii=False).encode('utf-8') if obj is not None else b''
        headers = [('Host', f'127.0.0.1:{port}')]
        if obj is not None:
            headers += [('Content-Type', 'application/json'), ('Content-Length', str(len(body)))]
        return send_raw(port, method, path, headers, body)

    def test_r14_a_second_relay_on_the_same_record_does_not_start(self):
        ex = self.base / 'p14'
        ex.mkdir()
        p1, port1, log1 = self.spawn(ex)
        self.wait_ready(p1, port1, log1)
        p2, port2, log2 = self.spawn(ex)
        self.assertNotEqual(p2.wait(20), 0)
        self.assertIn('別の中継が同じ記録を使っています', self.output(log2))
        self.assertEqual(self.call(port1, 'GET', '/record').status, 200, 'the first relay keeps running')
        p1.kill()
        p1.wait(10)
        p3, port3, log3 = self.spawn(ex)
        self.wait_ready(p3, port3, log3)

    def test_r21_a_relay_killed_at_each_step_restarts_and_the_resend_completes(self):
        expect = {'before_history': 1, 'after_history': 1, 'before_replace': 1, 'after_replace': 2}
        for stage, rev_after_crash in expect.items():
            with self.subTest(stage=stage):
                ex = self.base / f'p21-{stage}'
                ex.mkdir()
                A = cid(1)
                c1, c2, rid2 = make_case(A, question='一版目'), make_case(A, question='二版目'), new_rid()
                p, port, log = self.spawn(ex)
                self.wait_ready(p, port, log)
                r = self.call(port, 'POST', '/record/case', {'id': A, 'baseRev': 0, 'requestId': new_rid(), 'case': c1})
                self.assertEqual(r.status, 200, r)
                v1 = (ex / '_record' / 'cases' / f'{A}.json').read_bytes()
                p.kill()
                p.wait(10)
                p, port, log = self.spawn(ex, stage)
                self.wait_ready(p, port, log)
                try:
                    self.call(port, 'POST', '/record/case', {'id': A, 'baseRev': 1, 'requestId': rid2, 'case': c2})
                except OSError:
                    pass                                  # the relay died before answering
                self.assertEqual(p.wait(10), 9, self.output(log))
                p, port, log = self.spawn(ex)
                self.wait_ready(p, port, log)
                g = self.call(port, 'GET', '/record/case?id=' + A)
                self.assertEqual((g.status, g.json['rev']), (200, rev_after_crash), g)
                lst = self.call(port, 'GET', '/record').json
                self.assertFalse([c for c in lst['cases'] if c.get('problem')], lst)
                r = self.call(port, 'POST', '/record/case', {'id': A, 'baseRev': 1, 'requestId': rid2, 'case': c2})
                self.assertEqual((r.status, r.json.get('rev'), r.json.get('replayed')),
                                 (200, 2, stage == 'after_replace'), r)
                self.assertEqual((ex / '_record' / 'history' / A / '1.json').read_bytes(), v1)
                self.assertEqual(self.call(port, 'GET', '/record/case?id=' + A).json['case'], c2)
                p.kill()
                p.wait(10)


# =====================================================================================================
class LinkTests(RecordBase):
    """§3.5: no path of _record may lead outside it (R-16b symbolic links and junctions, R-16c hard links,
    R-16d a swap between the check and the open)."""

    def check_outcome(self, effect, outside_before):
        if effect == 'start':
            with self.assertRaises(rs.RecordStartError):
                self.open()
        else:
            self.open()
            if effect == 'settings':
                r = self.get('/record/settings')
                self.assertEqual((r.status, r.error), (409, 'record_corrupt'), r)
                self.assertNotIn(MARKER.encode(), r.body)
                r = self.post_settings({'note': '書けてはいけない'}, 2)
                self.assertEqual((r.status, r.error), (409, 'record_corrupt'), r)
                self.assertTrue(self.listing()['settings'].get('problem'))
                self.assert_case_ok(self.A, 2)
            else:
                self.assert_case_corrupt(self.A, 2)
                r = self.get('/record/settings')
                self.assertEqual(r.status, 200, r)
            self.assert_case_ok(self.B, 1)
            self.assertEqual(self.post_case(make_case(self.B, question='ほかの Case は書ける'), 1).status, 200)
            self.assertNotIn(MARKER.encode(), self.get('/record').body)
        self.assertEqual(tree(self.outside), outside_before, 'nothing outside _record changed')

    def test_r16b_links_at_every_level(self):
        A = cid(1)
        items = [('_record', 'dir', 'start'), ('cases', 'dir', 'start'), ('history', 'dir', 'start'),
                 ('history/_settings', 'dir', 'start'), ('.lock', 'file', 'start'),
                 ('settings.json', 'file', 'settings'), (f'cases/{A}.json', 'file', 'case'),
                 (f'history/{A}', 'dir', 'case'), (f'history/{A}/1.json', 'file', 'case')]
        for item, shape, effect in items:
            for kind in (('junction', 'symlink') if shape == 'dir' else ('symlink',)):
                with self.subTest(item=item, link=kind):
                    self.reset()
                    self.seed()
                    self.close()
                    target = self.move_outside(item)
                    self.make_link(self.root if item == '_record' else self.root / item, target, kind)
                    self.check_outcome(effect, tree(self.outside))

    def test_r16b_a_link_found_while_running(self):
        with self.subTest('cases folder becomes a junction: 500, then every record path 503'):
            self.seed()
            target = self.move_outside('cases')
            self.make_link(self.root / 'cases', target, 'junction')
            before = tree(self.outside)
            r = self.post_case(make_case(self.B, question='書けてはいけない'), 1)
            self.assertEqual(r.status, 500, r)
            for path in ('/record', '/record/settings', '/record/case?id=' + self.B):
                self.assertEqual(self.get(path).status, 503, path)
            self.assertEqual(tree(self.outside), before)
        with self.subTest('a Case folder in history becomes a junction: that Case 409'):
            self.reset()
            self.seed()
            target = self.move_outside(f'history/{self.A}')
            self.make_link(self.root / 'history' / self.A, target, 'junction')
            before = tree(self.outside)
            r = self.post_case(make_case(self.A, question='書けてはいけない'), 2)
            self.assertEqual((r.status, r.error), (409, 'record_corrupt'), r)
            self.assert_case_ok(self.B, 1)
            self.assertEqual(tree(self.outside), before)

    def test_r16c_hard_links(self):
        A = cid(1)
        items = [('.lock', 'start'), ('settings.json', 'settings'), ('history/_settings/1.json', 'settings'),
                 (f'cases/{A}.json', 'case'), (f'history/{A}/1.json', 'case')]
        for item, effect in items:
            with self.subTest(item=item):
                self.reset()
                self.seed()
                self.close()
                target = self.move_outside(item)
                os.link(target, self.root / item)
                self.check_outcome(effect, tree(self.outside))
        with self.subTest('two files inside _record hard-linked to each other'):
            self.reset()
            C = cid(3)
            self.write_revs(C, 1)
            self.close()
            self.hist_file(C, 1).parent.mkdir(parents=True)
            os.link(self.case_file(C), self.hist_file(C, 1))   # would otherwise be a valid orphan copy (§3.2)
            self.open()
            self.assert_case_corrupt(C, 1)
        with self.subTest('a current file hard-linked while running'):
            self.reset()
            self.seed()
            target = self.move_outside(f'cases/{A}.json')
            os.link(target, self.case_file(A))
            before = tree(self.outside)
            self.assert_case_corrupt(A, 2)
            self.assertEqual(tree(self.outside), before)

    def swap_hook(self, target, mode, source):
        fired = []
        want = os.path.normcase(os.path.abspath(target))

        def hook(path):
            if not fired and os.path.normcase(os.path.abspath(path)) == want:
                fired.append(path)
                os.remove(target)
                if mode == 'hard link':
                    os.link(source, target)
                else:
                    tmp = Path(target).with_name('swap-in.tmp.json')
                    tmp.write_bytes(Path(source).read_bytes())
                    os.replace(tmp, target)
        return mock.patch.object(rs, '_before_open', hook), fired

    def test_r16d_swap_between_the_check_and_the_open(self):
        A = cid(1)
        plans = [(f'cases/{A}.json', 'read'), ('settings.json', 'read settings'),
                 (f'history/{A}/1.json', 'startup'), ('.lock', 'startup lock')]
        for item, when in plans:
            for mode in (('hard link',) if item == '.lock' else ('hard link', 'other file')):
                with self.subTest(item=item, swap=mode):
                    self.reset()
                    self.seed()
                    if when.startswith('startup'):
                        self.close()              # (.lock cannot be copied while the relay holds its lock)
                    source = self.outside / ('swap-' + item.replace('/', '__'))
                    shutil.copy2(self.root / item, source)
                    if source.suffix == '.json':
                        remark(source)
                    before = tree(self.outside)
                    patch, fired = self.swap_hook(self.root / item, mode, source)
                    if when.startswith('startup'):
                        with patch:
                            if when == 'startup lock':
                                with self.assertRaises(rs.RecordStartError):
                                    self.open()
                            else:
                                self.open()
                        if when == 'startup':
                            self.assert_case_corrupt(A, 2)
                            self.assert_case_ok(self.B, 1)
                    else:
                        with patch:
                            r = self.get('/record/settings' if when == 'read settings' else '/record/case?id=' + A)
                        self.assertEqual((r.status, r.error), (409, 'record_corrupt'), r)
                        self.assertNotIn(MARKER.encode(), r.body)
                    self.assertTrue(fired, f'_before_open was called for {item}')
                    self.assertEqual(tree(self.outside), before)


if __name__ == '__main__':
    unittest.main()
