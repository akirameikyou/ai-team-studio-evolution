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

    def test_unsafe_path_parts_are_refused_and_nothing_is_written(self):
        r = self.post({'case': '..', 'task': 'T', 'to': 'GPT', 'route': 'agent', 'packet': 'p'})
        self.assertEqual(r['status'], 500)
        self.assertEqual(list(self.ex.rglob('*')), [])


if __name__ == '__main__':
    unittest.main()
