"""Tests for the Case archive in the relay (11/8, browser storage near its limit). Standard library only.

Run from operational-studio-001/proxy:   python -X utf8 -m unittest test_archive -v
The relay runs on a random local port against a temporary exchange folder. proxy/.env is never read.
An archive file is one Case in the Studio's own export form ({format: 'ats-operational-case', version: 1, case}),
so it can also be read back with the Studio's "JSONを読み込む".
"""
import hashlib
import json
import tempfile
import threading
import unittest
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import studio_proxy as sp

CID = 'CASE-20261021-001-abc123'


def export_text(cid=CID, **extra):
    return json.dumps({'format': 'ats-operational-case', 'version': 1, 'exportedAt': '2026-11-08T00:00:00.000Z',
                       'case': {'id': cid, 'question': '問い（日本語）', 'createdAt': '2026-10-21T00:00:00.000Z', **extra}},
                      ensure_ascii=False, indent=2)


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ex = Path(self._tmp.name)
        self._old = sp.EXCHANGE
        sp.EXCHANGE = self.ex
        self.srv = ThreadingHTTPServer(('127.0.0.1', 0), sp.Handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f'http://127.0.0.1:{self.srv.server_address[1]}'

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        sp.EXCHANGE = self._old
        self._tmp.cleanup()

    def post(self, body, origin=None, raw=None):
        data = raw if raw is not None else json.dumps(body, ensure_ascii=False).encode('utf-8')
        headers = {'Content-Type': 'application/json', **({'Origin': origin} if origin else {})}
        try:
            with urllib.request.urlopen(urllib.request.Request(self.base + '/archive', data=data, headers=headers), timeout=10) as r:
                return {'status': 200, **json.loads(r.read().decode('utf-8'))}
        except urllib.error.HTTPError as e:
            return {'status': e.code, **json.loads(e.read().decode('utf-8'))}

    def get(self, path, origin=None, **q):
        req = urllib.request.Request(f'{self.base}{path}?{urllib.parse.urlencode(q)}', headers={'Origin': origin} if origin else {})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return 200, json.loads(r.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode('utf-8'))

    def adir(self):
        return self.ex / '_archive'

    def test_a01_archive_is_saved_listed_and_read_back_exactly(self):
        text = export_text()
        r = self.post({'id': CID, 'text': text}, origin='http://localhost:8773')
        self.assertEqual(r['status'], 200)
        self.assertRegex(r['saved'], r'^CASE-20261021-001-abc123--\d{8}T\d{9}Z\.json$')
        f = self.adir() / r['saved']
        self.assertEqual(f.read_bytes(), text.encode('utf-8'))           # byte for byte, no newline translation
        self.assertEqual((r['bytes'], r['sha256']), (len(text.encode('utf-8')), hashlib.sha256(text.encode('utf-8')).hexdigest()))
        code, listing = self.get('/archives')
        self.assertEqual(code, 200)
        self.assertEqual([(a['id'], a['name'], a['bytes']) for a in listing['archives']], [(CID, r['saved'], r['bytes'])])
        self.assertEqual(listing['total'], 1)
        code, one = self.get('/archive', name=r['saved'])
        self.assertEqual((code, one['text'], one['sha256']), (200, text, r['sha256']))
        self.assertFalse([p for p in self.ex.rglob('*') if p.name.endswith('.tmp')])

    def test_a02_each_archive_is_a_new_file_nothing_is_overwritten(self):
        a = self.post({'id': CID, 'text': export_text(n=1)})['saved']
        b = self.post({'id': CID, 'text': export_text(n=2)})['saved']
        self.assertNotEqual(a, b)
        self.assertEqual(len(list(self.adir().iterdir())), 2)
        self.assertIn('"n": 1', (self.adir() / a).read_text(encoding='utf-8'))
        names = [x['name'] for x in self.get('/archives')[1]['archives']]
        self.assertEqual(names, sorted([a, b], reverse=True))             # newest first

    def test_a03_what_is_not_one_studio_case_is_refused_and_nothing_is_written(self):
        bad = [
            {'id': CID, 'text': 'not json'},
            {'id': CID, 'text': json.dumps({'format': 'other', 'version': 1, 'case': {'id': CID}})},
            {'id': CID, 'text': json.dumps({'format': 'ats-operational-case', 'version': 2, 'case': {'id': CID}})},
            {'id': CID, 'text': json.dumps({'format': 'ats-operational-case', 'version': 1, 'case': {'id': 'CASE-20261021-002-zzz999'}})},
            {'id': CID, 'text': json.dumps({'format': 'ats-operational-case', 'version': 1, 'case': [1]})},
            {'id': '../x', 'text': export_text('../x')},
            {'id': 'CASE-20261021-001-abc123\\..\\x', 'text': export_text('CASE-20261021-001-abc123\\..\\x')},
            {'id': CID},
            {'text': export_text()},
            {'id': CID, 'text': 5},
        ]
        for body in bad:
            self.assertEqual(self.post(body)['status'], 400, body)
        self.assertEqual(self.post(None, raw=b'{broken')['status'], 400)
        self.assertFalse(self.adir().exists())

    def test_a04_old_case_ids_without_a_suffix_are_accepted(self):
        old = 'CASE-20261007-001'
        self.assertEqual(self.post({'id': old, 'text': export_text(old)})['status'], 200)

    def test_a05_over_the_size_limit_is_refused(self):
        with mock.patch.object(sp, 'MAX_ARCHIVE', 200):
            r = self.post({'id': CID, 'text': export_text(pad='x' * 300)})
        self.assertEqual(r['status'], 413)
        self.assertFalse(self.adir().exists())

    def test_a06_archives_are_refused_to_pages_that_are_not_local(self):
        self.post({'id': CID, 'text': export_text()})
        for origin in ('https://akirameikyou.github.io', 'https://example.com', 'null'):
            self.assertEqual(self.post({'id': CID, 'text': export_text()}, origin=origin)['status'], 403, origin)
            self.assertEqual(self.get('/archives', origin=origin)[0], 403, origin)
            self.assertEqual(self.get('/archive', origin=origin, name='x')[0], 403, origin)
        self.assertEqual(len(list(self.adir().iterdir())), 1)

    def test_a07_names_outside_the_pattern_are_not_read(self):
        (self.ex / 'secret.json').write_text('{}', encoding='utf-8')
        self.adir().mkdir()
        (self.adir() / 'notes.json').write_text('{}', encoding='utf-8')
        for name in ('../secret.json', 'secret.json', 'notes.json', f'{CID}.json', ''):
            self.assertEqual(self.get('/archive', name=name)[0], 404, name)
        self.assertEqual(self.get('/archives')[1]['archives'], [])

    def test_a08_links_are_neither_listed_nor_read_and_a_linked_folder_is_not_used(self):
        a = self.post({'id': CID, 'text': export_text()})['saved']
        real = Path.is_symlink
        with mock.patch.object(Path, 'is_symlink', lambda p: p.name == a or real(p)):
            self.assertEqual(self.get('/archives')[1]['archives'], [])
            self.assertEqual(self.get('/archive', name=a)[0], 404)
        folder = str(self.adir())
        with mock.patch.object(Path, 'is_symlink', lambda p: str(p) == folder or real(p)):
            self.assertEqual(self.get('/archives')[1]['archives'], [])
            self.assertEqual(self.post({'id': CID, 'text': export_text()})['status'], 409)
        self.assertEqual(len(list(self.adir().iterdir())), 1)

    def test_a09_reply_reports_how_much_is_kept(self):
        t = export_text()
        n = len(t.encode('utf-8'))
        a = self.post({'id': CID, 'text': t})
        b = self.post({'id': 'CASE-20261021-002-zzz999', 'text': export_text('CASE-20261021-002-zzz999')})
        self.assertEqual((a['count'], a['totalBytes']), (1, n))
        self.assertEqual(b['count'], 2)
        self.assertEqual(self.get('/archives')[1]['totalBytes'], a['bytes'] + b['bytes'])


if __name__ == '__main__':
    unittest.main()
