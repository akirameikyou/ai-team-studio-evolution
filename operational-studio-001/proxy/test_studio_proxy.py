"""Tests for the local relay's file handling (stage 2-1B). Standard library only.

Run from operational-studio-001/proxy:   python -m unittest test_studio_proxy -v
The relay runs on a random local port against a temporary exchange folder. proxy/.env is never read
(load_env() is only called by main()), and no external API is called (only the agent and mock routes).
"""
import hashlib
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.request
from unittest import mock
from http.server import ThreadingHTTPServer
from pathlib import Path

import studio_proxy as sp

PACKET = '[AI TEAM STUDIO｜観測依頼]  CASE: C1  TASK: C1-OBS-GPT  TO: GPT\n\n■ 問い\n一行目\n\n三行目（空行あり）\n'


class RelayFileTests(unittest.TestCase):
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

    def post(self, body):
        req = urllib.request.Request(self.base + '/task', data=json.dumps(body).encode('utf-8'),
                                     headers={'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return json.loads(r.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            return {'status': e.code, **json.loads(e.read().decode('utf-8'))}

    def result(self, case, task, to):
        q = urllib.parse.urlencode({'case': case, 'task': task, 'to': to})
        with urllib.request.urlopen(f'{self.base}/result?{q}', timeout=10) as r:
            return json.loads(r.read().decode('utf-8'))

    def task_dir(self, case='C1', task='C1-OBS-GPT'):
        return self.ex / case / task

    def no_temp_files(self):
        return not [p for p in self.ex.rglob('*') if p.name.endswith('.tmp')]

    def test_agent_route_writes_the_packet_byte_for_byte(self):
        r = self.post({'case': 'C1', 'task': 'C1-OBS-GPT', 'to': 'GPT', 'route': 'agent', 'packet': PACKET})
        self.assertTrue(r['queued'])
        written = (self.task_dir() / 'to-GPT.md').read_bytes()
        self.assertEqual(written, PACKET.encode('utf-8'))
        self.assertNotIn(b'\r\n', written)
        self.assertEqual(hashlib.sha256(written).hexdigest(), hashlib.sha256(PACKET.encode('utf-8')).hexdigest())
        self.assertTrue(self.no_temp_files())

    def test_mock_route_writes_request_and_reply_exactly(self):
        r = self.post({'case': 'C1', 'task': 'C1-OBS-GPT', 'to': 'GPT', 'route': 'mock', 'packet': PACKET})
        self.assertIn('[STUDIO_RESULT]', r['text'])
        self.assertEqual((self.task_dir() / 'to-GPT.md').read_bytes(), PACKET.encode('utf-8'))
        self.assertEqual((self.task_dir() / 'from-GPT.md').read_bytes(), r['text'].encode('utf-8'))
        self.assertTrue(self.no_temp_files())

    def test_rewriting_replaces_the_file_completely(self):
        self.post({'case': 'C1', 'task': 'C1-OBS-GPT', 'to': 'GPT', 'route': 'agent', 'packet': 'x' * 1000})
        self.post({'case': 'C1', 'task': 'C1-OBS-GPT', 'to': 'GPT', 'route': 'agent', 'packet': 'short'})
        self.assertEqual((self.task_dir() / 'to-GPT.md').read_bytes(), b'short')
        self.assertTrue(self.no_temp_files())

    def test_result_is_pending_while_missing_or_fresh(self):
        self.assertEqual(self.result('C1', 'C1-OBS-GPT', 'GPT'), {'pending': True})
        d = self.task_dir()
        d.mkdir(parents=True)
        (d / 'from-GPT.md').write_bytes('[STUDIO_RESULT]\n途中'.encode('utf-8'))
        self.assertEqual(self.result('C1', 'C1-OBS-GPT', 'GPT'), {'pending': True, 'settling': True})

    def test_settled_result_is_returned_with_lf_newlines(self):
        d = self.task_dir()
        d.mkdir(parents=True)
        f = d / 'from-GPT.md'
        f.write_bytes('一行目\r\n二行目\r\n'.encode('utf-8'))
        old = time.time() - sp.SETTLE_SECONDS - 1
        os.utime(f, (old, old))
        self.assertEqual(self.result('C1', 'C1-OBS-GPT', 'GPT'), {'text': '一行目\n二行目\n'})

    def test_result_waits_at_exactly_the_settle_boundary(self):
        d = self.task_dir()
        d.mkdir(parents=True)
        f = d / 'from-GPT.md'
        f.write_bytes(b'done')
        mtime = f.stat().st_mtime
        with mock.patch.object(sp.time, 'time', return_value=mtime + sp.SETTLE_SECONDS):
            self.assertEqual(self.result('C1', 'C1-OBS-GPT', 'GPT'), {'pending': True, 'settling': True})
        with mock.patch.object(sp.time, 'time', return_value=mtime + sp.SETTLE_SECONDS + 0.01):
            self.assertEqual(self.result('C1', 'C1-OBS-GPT', 'GPT'), {'text': 'done'})

    def test_replace_that_keeps_failing_answers_500_keeps_the_old_file_and_leaves_no_temp(self):
        self.post({'case': 'C1', 'task': 'C1-OBS-GPT', 'to': 'GPT', 'route': 'agent', 'packet': 'old'})
        with mock.patch.object(sp, 'REPLACE_WAIT', 0), \
                mock.patch.object(sp.os, 'replace', side_effect=PermissionError('in use')) as rep:
            r = self.post({'case': 'C1', 'task': 'C1-OBS-GPT', 'to': 'GPT', 'route': 'agent', 'packet': 'new'})
        self.assertEqual(r['status'], 500)
        self.assertEqual(rep.call_count, sp.REPLACE_ATTEMPTS)
        self.assertEqual((self.task_dir() / 'to-GPT.md').read_bytes(), b'old')
        self.assertTrue(self.no_temp_files())

    def test_replace_that_fails_briefly_is_retried(self):
        real = os.replace
        calls = {'n': 0}

        def flaky(src, dst):
            calls['n'] += 1
            if calls['n'] <= 2:
                raise PermissionError('in use')
            return real(src, dst)
        with mock.patch.object(sp, 'REPLACE_WAIT', 0), mock.patch.object(sp.os, 'replace', side_effect=flaky):
            r = self.post({'case': 'C1', 'task': 'C1-OBS-GPT', 'to': 'GPT', 'route': 'agent', 'packet': PACKET})
        self.assertTrue(r['queued'])
        self.assertEqual(calls['n'], 3)
        self.assertEqual((self.task_dir() / 'to-GPT.md').read_bytes(), PACKET.encode('utf-8'))
        self.assertTrue(self.no_temp_files())

    def test_concurrent_writes_to_one_file_leave_one_complete_version(self):
        packets = [f'{i:02d}' + 'x' * (2000 + i * 37) for i in range(12)]
        threads = [threading.Thread(target=self.post, args=({'case': 'C1', 'task': 'C1-OBS-GPT', 'to': 'GPT',
                                                             'route': 'agent', 'packet': p},)) for p in packets]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        final = (self.task_dir() / 'to-GPT.md').read_bytes().decode('utf-8')
        self.assertIn(final, packets)
        self.assertTrue(self.no_temp_files())

    def get(self, path, **q):
        with urllib.request.urlopen(f'{self.base}{path}?{urllib.parse.urlencode(q)}', timeout=10) as r:
            return json.loads(r.read().decode('utf-8'))

    def test_case_exists_reports_folders_without_creating_any(self):
        self.assertEqual(self.get('/case-exists', case='CASE-20261021-001-abc123'), {'exists': False})
        self.assertEqual(list(self.ex.iterdir()), [])
        self.post({'case': 'CASE-20261021-001-abc123', 'task': 'T', 'to': 'GPT', 'route': 'agent', 'packet': 'p'})
        self.assertEqual(self.get('/case-exists', case='CASE-20261021-001-abc123'), {'exists': True})
        self.assertEqual(self.get('/case-exists', case='CASE-20261021-001-zzz999'), {'exists': False})

    STORE = json.dumps({'version': 1, 'cases': {'CASE-20261021-001-abc123': {'question': '問い'}}, 'order': []}, ensure_ascii=False)

    def snap(self, body, origin=None, raw=None):
        data = raw if raw is not None else json.dumps(body).encode('utf-8')
        headers = {'Content-Type': 'application/json'}
        if origin:
            headers['Origin'] = origin
        req = urllib.request.Request(self.base + '/snapshot', data=data, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return json.loads(r.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            return {'status': e.code, **json.loads(e.read().decode('utf-8'))}

    def get_status(self, path, origin=None, **q):
        req = urllib.request.Request(f'{self.base}{path}?{urllib.parse.urlencode(q)}', headers={'Origin': origin} if origin else {})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return 200, json.loads(r.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode('utf-8'))

    def test_snapshot_is_saved_listed_and_read_back_exactly(self):
        r = self.snap({'rev': 3, 'store': self.STORE}, origin='http://localhost:8773')
        self.assertRegex(r['saved'], r'^ats-store-\d{8}T\d{9}Z-r3\.json$')
        f = self.ex / '_snapshots' / r['saved']
        self.assertEqual(f.read_bytes(), self.STORE.encode('utf-8'))
        self.assertEqual(r['sha256'], hashlib.sha256(self.STORE.encode('utf-8')).hexdigest())
        code, listing = self.get_status('/snapshots')
        self.assertEqual((code, [s['name'] for s in listing['snapshots']], listing['total']), (200, [r['saved']], 1))
        code, one = self.get_status('/snapshot', name=r['saved'])
        self.assertEqual((code, one['text']), (200, self.STORE))
        self.assertTrue(self.no_temp_files())

    def test_each_snapshot_is_a_new_file(self):
        names = {self.snap({'rev': 1, 'store': self.STORE})['saved'] for _ in range(5)}
        self.assertEqual(len(names), 5)
        self.assertEqual(len(list((self.ex / '_snapshots').iterdir())), 5)

    def test_snapshot_that_is_not_a_studio_record_is_refused_and_nothing_is_written(self):
        for store in ('not json', json.dumps({'version': 2, 'cases': {}}), json.dumps([1, 2])):
            r = self.snap({'rev': 1, 'store': store})
            self.assertEqual(r['status'], 400, store)
        self.assertEqual(self.snap({'rev': 1})['status'], 400)
        self.assertFalse((self.ex / '_snapshots').exists())

    def test_snapshot_over_the_size_limit_is_refused(self):
        with mock.patch.object(sp, 'MAX_SNAPSHOT', 100):
            r = self.snap({'rev': 1, 'store': self.STORE + ' ' * 200})
        self.assertEqual(r['status'], 413)
        self.assertFalse((self.ex / '_snapshots').exists())

    def test_snapshots_are_refused_to_pages_that_are_not_local(self):
        self.snap({'rev': 1, 'store': self.STORE})
        self.assertEqual(self.snap({'rev': 1, 'store': self.STORE}, origin='https://akirameikyou.github.io')['status'], 403)
        self.assertEqual(self.get_status('/snapshots', origin='https://akirameikyou.github.io')[0], 403)
        self.assertEqual(self.get_status('/snapshot', origin='https://example.com', name='x')[0], 403)
        self.assertEqual(len(list((self.ex / '_snapshots').iterdir())), 1)

    def test_snapshot_reply_reports_how_much_is_kept(self):
        a = self.snap({'rev': 1, 'store': self.STORE})
        b = self.snap({'rev': 2, 'store': self.STORE})
        n = len(self.STORE.encode('utf-8'))
        self.assertEqual((a['count'], a['totalBytes']), (1, n))
        self.assertEqual((b['count'], b['totalBytes']), (2, 2 * n))
        self.assertEqual(self.get_status('/snapshots')[1]['totalBytes'], 2 * n)

    def test_symbolic_links_in_the_snapshot_folder_are_neither_listed_nor_read(self):
        self.snap({'rev': 1, 'store': self.STORE})
        outside = self.ex / 'outside.json'
        outside.write_text('{"secret": true}', encoding='utf-8')
        link = self.ex / '_snapshots' / 'ats-store-20261021T000000000Z-r9.json'
        try:
            os.symlink(outside, link)
        except (OSError, NotImplementedError) as e:
            self.skipTest(f'cannot create a symbolic link here: {e}')
        names = [s['name'] for s in self.get_status('/snapshots')[1]['snapshots']]
        self.assertNotIn(link.name, names)
        self.assertEqual(self.get_status('/snapshot', name=link.name)[0], 404)

    def test_files_reported_as_links_are_excluded_even_without_symlink_rights(self):
        a = self.snap({'rev': 1, 'store': self.STORE})['saved']
        b = self.snap({'rev': 2, 'store': self.STORE})['saved']
        real = Path.is_symlink
        with mock.patch.object(Path, 'is_symlink', lambda p: p.name == a or real(p)):
            self.assertEqual([p.name for p in sp.snapshot_files()], [b])
            self.assertEqual(self.get_status('/snapshot', name=a)[0], 404)
            self.assertEqual(self.get_status('/snapshot', name=b)[0], 200)

    def test_a_snapshot_folder_that_is_a_link_is_not_used(self):
        a = self.snap({'rev': 1, 'store': self.STORE})['saved']
        folder = str(self.ex / '_snapshots')
        real = Path.is_symlink
        with mock.patch.object(Path, 'is_symlink', lambda p: str(p) == folder or real(p)):
            self.assertEqual(self.get_status('/snapshots')[1]['snapshots'], [])
            self.assertEqual(self.get_status('/snapshot', name=a)[0], 404)
            self.assertEqual(self.snap({'rev': 2, 'store': self.STORE})['status'], 409)
        self.assertEqual(len(list((self.ex / '_snapshots').iterdir())), 1)

    def test_snapshot_names_outside_the_pattern_are_not_read(self):
        (self.ex / 'secret.json').write_text('{}', encoding='utf-8')
        for name in ('../secret.json', 'secret.json', 'ats-store-x.json', ''):
            self.assertEqual(self.get_status('/snapshot', name=name)[0], 404, name)

    def test_studio_config_is_served_from_the_file_next_to_the_relay(self):
        code, cfg = self.get_status('/studio-config')
        self.assertEqual(code, 200)
        on_disk = json.loads((Path(sp.__file__).parent / 'studio.config.json').read_text(encoding='utf-8'))
        self.assertEqual(cfg, on_disk)
        self.assertEqual(cfg['version'], 1)
        text = json.dumps(cfg)
        self.assertNotIn('API_KEY', text)          # no secrets in the defaults (keys stay in proxy/.env)
        self.assertNotIn('AIza', text)
        self.assertEqual(set(cfg), {'version', 'note', 'proxyUrl', 'observers'})

    def test_studio_config_missing_answers_404(self):
        with mock.patch.object(sp, 'HERE', self.ex):
            self.assertEqual(self.get_status('/studio-config')[0], 404)

    def test_unsafe_path_parts_are_refused_and_nothing_is_written(self):
        r = self.post({'case': '..', 'task': 'T', 'to': 'GPT', 'route': 'agent', 'packet': 'p'})
        self.assertEqual(r['status'], 500)
        self.assertEqual(list(self.ex.rglob('*')), [])


if __name__ == '__main__':
    unittest.main()
