import json, os, sys, tempfile, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import codex_runner as R

FAKE = R.find_codex(str(HERE / 'fake_codex.py'))


def packet(task):
    return f'[AI TEAM STUDIO｜観測依頼]  CASE: CASE-X  TASK: {task}  TO: Codex\n\n■ 問い\n日本語の問い\r\n改行も保つ\n'


class RunnerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.ex = Path(self.tmp.name) / 'exchange'
        self.rec = Path(self.tmp.name) / 'calls.jsonl'
        os.environ['FAKE_RECORD'] = str(self.rec); os.environ['FAKE_MODE'] = 'ok'
        os.environ['FAKE_REPLY'] = '[STUDIO_RESULT]\nTASK: CASE-X-OBS-Codex\n本文\n[/STUDIO_RESULT]'

    def tearDown(self):
        self.tmp.cleanup()

    def task(self, name, text=None, case='CASE-X'):
        d = self.ex / case / name; d.mkdir(parents=True)
        (d / 'to-Codex.md').write_bytes((text if text is not None else packet(name)).encode('utf-8'))
        return d

    def calls(self):
        return [json.loads(l) for l in self.rec.read_text(encoding='utf-8').splitlines()] if self.rec.exists() else []

    def run_(self, d, timeout=60):
        return R.run_task(d, FAKE, self.ex.parent, timeout)

    def test_only_waiting_observation_and_synthesis_tasks_are_picked(self):
        obs, syn, syn2 = self.task('CASE-X-OBS-Codex'), self.task('CASE-X-SYN'), self.task('CASE-X-SYN-2')
        self.task('CASE-X-NEXT'); self.task('CASE-X-IMPL-Codex'); self.task('CASE-X-OBS-Codex', case='_inbox')
        done = self.task('CASE-Y-OBS-Codex', case='CASE-Y'); (done / 'from-Codex.md').write_text('x')
        claimed = self.task('CASE-Z-OBS-Codex', case='CASE-Z'); (claimed / R.CLAIM).write_text('{}')
        self.assertEqual(set(R.waiting_tasks(self.ex)), {obs, syn, syn2})

    def claim(self, d):
        return json.loads((d / R.CLAIM).read_text(encoding='utf-8'))

    def test_reply_is_published_byte_for_byte_and_the_claim_records_the_run(self):
        d = self.task('CASE-X-OBS-Codex')
        self.assertEqual(self.run_(d), 'published')
        self.assertEqual((d / 'from-Codex.md').read_bytes(), os.environ['FAKE_REPLY'].encode('utf-8'))
        self.assertFalse((d / R.TMP).exists())
        c = self.claim(d)
        self.assertEqual(c['state'], 'done'); self.assertIn('since', c); self.assertIsInstance(c['seconds'], int)
        self.assertEqual(c['condition'], {'route': 'runner', 'runner': R.RUNNER_VERSION, 'preamble': R.PREAMBLE_VERSION,
                                          'codex': 'fake-codex 0.0', 'sandbox': 'read-only', 'effort': 'default'})
        self.assertIn('from-Codex.md を書きました', (d / R.LOG).read_text(encoding='utf-8'))
        self.assertNotIn(d, R.waiting_tasks(self.ex))          # a done folder is never run again

    def test_effort_is_passed_to_codex_and_recorded(self):
        d = self.task('CASE-X-OBS-Codex')
        R.run_task(d, FAKE, self.ex.parent, 60, effort='medium')
        (c,) = self.calls()
        self.assertIn('model_reasoning_effort="medium"', c['args'])
        self.assertEqual(self.claim(d)['condition']['effort'], 'medium')
        d2 = self.task('CASE-Y-OBS-Codex', case='CASE-Y'); self.run_(d2)
        self.assertFalse(any('model_reasoning_effort' in x for x in self.calls()[1]['args']))   # no effort: Codex's own default

    def test_codex_runs_read_only_ephemeral_without_approvals_and_gets_the_exact_request(self):
        d = self.task('CASE-X-OBS-Codex'); self.run_(d)
        (c,) = self.calls()
        a = c['args']
        self.assertEqual(a[:1], ['exec']); self.assertIn('--ephemeral', a)
        self.assertEqual(a[a.index('-s') + 1], 'read-only'); self.assertIn('approval_policy="never"', a)
        self.assertEqual(a[-1], '-'); self.assertNotIn('--dangerously-bypass-approvals-and-sandbox', a)
        self.assertEqual(c['stdin'], R.PREAMBLE + packet('CASE-X-OBS-Codex') + R.POSTAMBLE)   # CRLF inside kept

    def test_request_for_another_task_is_not_sent(self):
        d = self.task('CASE-X-OBS-Codex', text=packet('CASE-X-SYN'))
        self.assertEqual(self.run_(d), 'failed'); self.assertEqual(self.calls(), [])
        self.assertFalse((d / 'from-Codex.md').exists())
        self.assertEqual(json.loads((d / R.CLAIM).read_text(encoding='utf-8'))['state'], 'failed')

    def test_failure_empty_reply_and_timeout_publish_nothing_and_leave_a_failed_claim(self):
        for mode, timeout in (('fail', 60), ('empty', 60), ('slow', 2)):
            with self.subTest(mode=mode):
                os.environ['FAKE_MODE'] = mode
                d = self.task('CASE-X-OBS-Codex', case='CASE-' + mode)
                self.assertEqual(self.run_(d, timeout), 'failed')
                self.assertFalse((d / 'from-Codex.md').exists()); self.assertFalse((d / R.TMP).exists())
                self.assertEqual(json.loads((d / R.CLAIM).read_text(encoding='utf-8'))['state'], 'failed')
                self.assertNotIn(d, R.waiting_tasks(self.ex))     # a person looks before it runs again

    def test_a_reply_a_person_wrote_first_is_never_overwritten(self):
        os.environ['FAKE_MODE'] = 'person-first'
        d = self.task('CASE-X-OBS-Codex')
        self.assertEqual(self.run_(d), 'conflict')
        self.assertEqual((d / 'from-Codex.md').read_text(encoding='utf-8'), '人が書いた返事')
        self.assertEqual((d / R.CONFLICT).read_text(encoding='utf-8'), 'runner reply')

    def test_a_claimed_folder_is_not_run_twice(self):
        d = self.task('CASE-X-OBS-Codex'); (d / R.CLAIM).write_text('{"state":"running"}')
        self.assertEqual(self.run_(d), 'skipped'); self.assertEqual(self.calls(), [])

    def test_dry_run_sends_nothing_and_claims_nothing(self):
        d = self.task('CASE-X-OBS-Codex')
        self.assertEqual(R.run_task(d, FAKE, self.ex.parent, 60, dry_run=True), 'dry-run')
        self.assertEqual(self.calls(), []); self.assertFalse((d / R.CLAIM).exists())

    def test_execution_tasks_are_refused_by_task_option(self):
        d = self.task('CASE-X-NEXT')
        with self.assertRaises(SystemExit):
            R.main(['--codex', str(HERE / 'fake_codex.py'), '--exchange', str(self.ex), '--task', str(d)])
        self.assertEqual(self.calls(), [])


if __name__ == '__main__':
    unittest.main()
