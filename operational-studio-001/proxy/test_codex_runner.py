import hashlib, json, os, sys, tempfile, unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import codex_runner as R

FAKE = R.find_codex(str(HERE / 'fake_codex.py'))
sha = lambda b: hashlib.sha256(b).hexdigest()


def packet(task, case='CASE-X', to='Codex'):
    # The last block of a real request repeats TASK (a template for the reply): only the header line counts (R3).
    return (f'[AI TEAM STUDIO｜観測依頼]  CASE: {case}  TASK: {task}  TO: {to}\n\n■ 問い\n日本語の問い\r\n改行も保つ\n\n'
            f'[STUDIO_RESULT]\nCASE: {case}\nTASK: {task}\nFROM: Codex\n[/STUDIO_RESULT]\n')


class RunnerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.ex = Path(self.tmp.name) / 'exchange'
        self.rec = Path(self.tmp.name) / 'calls.jsonl'          # written by fake_codex.py in its -C folder
        self.reply = '[STUDIO_RESULT]\nTASK: CASE-X-OBS-Codex\n本文\n[/STUDIO_RESULT]'
        self.fake('ok')

    def tearDown(self):
        self.tmp.cleanup()

    def fake(self, mode, occupy=()):
        (Path(self.tmp.name) / 'fake.json').write_text(json.dumps({'mode': mode, 'reply': self.reply, 'occupy': list(occupy)}), encoding='utf-8')

    def task(self, name, text=None, case='CASE-X'):
        d = self.ex / case / name; d.mkdir(parents=True)
        (d / 'to-Codex.md').write_bytes((text if text is not None else packet(name, case)).encode('utf-8'))
        return d

    def calls(self):
        return [json.loads(l) for l in self.rec.read_text(encoding='utf-8').splitlines()] if self.rec.exists() else []

    def run_(self, d, timeout=60, **kw):
        return R.run_task(d, FAKE, self.ex.parent, timeout, **kw)

    def claim(self, d):
        return json.loads((d / R.CLAIM).read_text(encoding='utf-8'))

    def log(self, d):
        return (d / R.LOG).read_text(encoding='utf-8')

    # --- choosing tasks ---
    def test_only_waiting_observation_and_synthesis_tasks_are_picked(self):
        obs, syn, syn2 = self.task('CASE-X-OBS-Codex'), self.task('CASE-X-SYN'), self.task('CASE-X-SYN-2')
        self.task('CASE-X-NEXT'); self.task('CASE-X-IMPL-Codex'); self.task('CASE-X-OBS-Codex', case='_inbox')
        done = self.task('CASE-Y-OBS-Codex', case='CASE-Y'); (done / 'from-Codex.md').write_text('x')
        claimed = self.task('CASE-Z-OBS-Codex', case='CASE-Z'); (claimed / R.CLAIM).write_text('{}')
        self.assertEqual(set(R.waiting_tasks(self.ex)), {obs, syn, syn2})

    def test_impl_requires_task_write_explicit_repo_and_git_worktree(self):
        d = self.task('CASE-X-IMPL-Codex')
        with self.assertRaises(SystemExit):
            R.main(['--codex', str(HERE / 'fake_codex.py'), '--exchange', str(self.ex), '--task', str(d)])
        self.assertEqual(self.calls(), [])
        with self.assertRaises(SystemExit):
            R.main(['--codex', str(HERE / 'fake_codex.py'), '--exchange', str(self.ex), '--task', str(d), '--write'])
        self.assertEqual(self.calls(), [])
        R.main(['--codex', str(HERE / 'fake_codex.py'), '--exchange', str(self.ex), '--repo', self.tmp.name,
                '--task', str(d), '--write'])
        self.assertEqual(self.calls(), [])
        claim = self.claim(d)
        self.assertEqual(claim['state'], 'failed'); self.assertEqual(claim['gitRecord'], '記録できなかった')
        self.assertIn('記録できなかった', self.log(d))

    def test_impl_is_not_picked_by_once_watch_or_dry_run_and_dry_run_explains_write(self):
        impl = self.task('CASE-X-IMPL-Codex')
        obs = self.task('CASE-X-OBS-Codex')
        self.assertEqual(R.waiting_tasks(self.ex), [obs])
        R.main(['--codex', str(HERE / 'fake_codex.py'), '--exchange', str(self.ex), '--repo', self.tmp.name, '--once'])
        self.assertFalse((impl / R.CLAIM).exists())
        self.assertEqual(len(self.calls()), 1)
        prior_calls = len(self.calls())
        (self.ex / R.STOP).write_text('', encoding='utf-8')
        R.main(['--codex', str(HERE / 'fake_codex.py'), '--exchange', str(self.ex), '--repo', self.tmp.name, '--watch', '1'])
        self.assertFalse((impl / R.CLAIM).exists())
        self.assertEqual(len(self.calls()), prior_calls)
        from contextlib import redirect_stdout
        import io
        output = io.StringIO()
        with redirect_stdout(output):
            R.main(['--codex', str(HERE / 'fake_codex.py'), '--exchange', str(self.ex), '--dry-run'])
        self.assertIn('--write', output.getvalue())
        self.assertEqual(len(self.calls()), prior_calls)

    def make_git_repo(self):
        repo = Path(self.tmp.name) / 'repo'; repo.mkdir()
        def git(*args):
            return __import__('subprocess').run(['git', '-C', str(repo), *args], capture_output=True, check=True)
        git('init', '-q')
        git('config', 'user.name', 'Test'); git('config', 'user.email', 'test@example.invalid')
        (repo / 'base.txt').write_text('base', encoding='utf-8')
        git('add', 'base.txt'); git('commit', '-qm', 'initial')
        (repo / 'fake.json').write_text(json.dumps({'mode': 'ok', 'reply': self.reply}), encoding='utf-8')
        self.rec = repo / 'calls.jsonl'
        return repo

    def test_impl_uses_workspace_write_and_records_git_before_after(self):
        repo = self.make_git_repo()
        d = self.task('CASE-X-IMPL-Codex')
        R.main(['--codex', str(HERE / 'fake_codex.py'), '--exchange', str(self.ex), '--repo', str(repo),
                '--task', str(d), '--write'])
        (call,) = self.calls()
        args = call['args']
        self.assertEqual(args[args.index('-s') + 1], 'workspace-write')
        self.assertIn('approval_policy="never"', args)
        self.assertIn(R.PREAMBLE_IMPL, call['stdin'])
        claim = self.claim(d)
        self.assertEqual(claim['condition']['sandbox'], 'workspace-write')
        self.assertEqual(claim['condition']['preamble'], R.PREAMBLE_IMPL_VERSION)
        self.assertIn('gitBefore', claim); self.assertIn('gitAfter', claim)
        self.assertEqual(claim['gitBefore']['head'], claim['gitAfter']['head'])
        self.assertIn('HEAD', self.log(d)); self.assertIn('status', self.log(d))

    def test_impl_records_head_change_warning(self):
        repo = self.make_git_repo()
        d = self.task('CASE-X-IMPL-Codex')
        before = {'head': 'a' * 40, 'status': []}
        after = {'head': 'b' * 40, 'status': ['changed.txt']}
        with mock.patch.object(R, 'git_snapshot', side_effect=[before, after]):
            self.assertEqual(R.run_task(d, FAKE, repo, 60, write=True), 'published')
        claim = self.claim(d)
        self.assertIn('HEAD が実行前後で変化', claim['gitWarning'])
        self.assertIn('警告: HEAD が変化', self.log(d))
        self.assertEqual(claim['changedFiles'], ['changed.txt'])

    # --- publishing (R1, R2, S1) ---
    def test_reply_is_published_byte_for_byte_and_the_claim_records_the_run_and_its_hash(self):
        d = self.task('CASE-X-OBS-Codex')
        self.assertEqual(self.run_(d), 'published')
        body = (d / 'from-Codex.md').read_bytes()
        self.assertEqual(body, self.reply.encode('utf-8'))
        self.assertFalse((d / R.TMP).exists())
        c = self.claim(d)
        self.assertEqual(c['state'], 'done'); self.assertIn('since', c); self.assertIsInstance(c['seconds'], int)
        self.assertEqual(c['replySha256'], sha(body))                                  # S1-1
        self.assertEqual(c['condition'], {'route': 'runner', 'runner': R.RUNNER_VERSION, 'preamble': R.PREAMBLE_VERSION,
                                          'codex': 'fake-codex 0.0', 'sandbox': 'read-only', 'effort': 'default',
                                          'modelReported': 'fake-model', 'effortReported': 'none'})
        self.assertIn('from-Codex.md を書きました', self.log(d))
        self.assertNotIn(d, R.waiting_tasks(self.ex))          # a done folder is never run again

    def test_codex_runs_read_only_ephemeral_without_approvals_and_gets_the_exact_request(self):
        d = self.task('CASE-X-OBS-Codex'); self.run_(d)
        (c,) = self.calls()
        a = c['args']
        self.assertEqual(a[:1], ['exec']); self.assertIn('--ephemeral', a)
        self.assertEqual(a[a.index('-s') + 1], 'read-only'); self.assertIn('approval_policy="never"', a)
        self.assertEqual(a[-1], '-'); self.assertNotIn('--dangerously-bypass-approvals-and-sandbox', a)
        self.assertEqual(c['stdin'], R.PREAMBLE + packet('CASE-X-OBS-Codex') + R.POSTAMBLE)   # CRLF inside kept

    def test_a_reply_a_person_wrote_first_is_never_overwritten(self):
        self.fake('person-first')
        d = self.task('CASE-X-OBS-Codex')
        self.assertEqual(self.run_(d), 'conflict')
        self.assertEqual((d / 'from-Codex.md').read_text(encoding='utf-8'), '人が書いた返事')
        self.assertEqual((d / R.CONFLICT).read_text(encoding='utf-8'), 'runner reply')
        c = self.claim(d)                                                                 # S1-3
        self.assertEqual(c['state'], 'conflict'); self.assertEqual(c['saved'], R.CONFLICT)
        self.assertEqual(c['savedSha256'], sha(b'runner reply')); self.assertNotIn('replySha256', c)
        self.assertFalse((d / R.TMP).exists())

    def test_r1_conflict_files_are_never_overwritten_either(self):
        self.fake('person-first', occupy=[R.CONFLICT, 'from-Codex.runner-2.md'])
        d = self.task('CASE-X-OBS-Codex')
        self.assertEqual(self.run_(d), 'conflict')
        self.assertEqual((d / R.CONFLICT).read_text(encoding='utf-8'), '先にあった退避ファイル ' + R.CONFLICT)
        self.assertEqual((d / 'from-Codex.runner-2.md').read_text(encoding='utf-8'), '先にあった退避ファイル from-Codex.runner-2.md')
        self.assertEqual((d / 'from-Codex.runner-3.md').read_text(encoding='utf-8'), 'runner reply')
        self.assertEqual(self.claim(d)['saved'], 'from-Codex.runner-3.md')
        self.assertFalse((d / R.TMP).exists())

    def test_r1_an_unexpected_error_while_publishing_leaves_a_failed_claim_no_temp_and_the_log(self):
        d = self.task('CASE-X-OBS-Codex')
        with mock.patch.object(R.os, 'link', side_effect=PermissionError('locked')):
            self.assertEqual(self.run_(d), 'failed')
        c = self.claim(d)                                                                 # S1-2, S1-4
        self.assertEqual(c['state'], 'failed'); self.assertIn('PermissionError', c['reason'])
        self.assertFalse((d / 'from-Codex.md').exists()); self.assertFalse((d / R.TMP).exists())
        self.assertIn('PermissionError', self.log(d))

    def test_failure_empty_reply_and_timeout_publish_nothing_and_leave_a_failed_claim(self):
        for mode, timeout in (('fail', 60), ('empty', 60), ('slow', 2)):
            with self.subTest(mode=mode):
                self.fake(mode)
                d = self.task('CASE-X-OBS-Codex', case='CASE-' + mode)
                self.assertEqual(self.run_(d, timeout), 'failed')
                self.assertFalse((d / 'from-Codex.md').exists()); self.assertFalse((d / R.TMP).exists())
                self.assertEqual(self.claim(d)['state'], 'failed')
                self.assertNotIn(d, R.waiting_tasks(self.ex))     # a person looks before it runs again

    def test_a_claimed_folder_is_not_run_twice(self):
        d = self.task('CASE-X-OBS-Codex'); (d / R.CLAIM).write_text('{"state":"running"}')
        self.assertEqual(self.run_(d), 'skipped'); self.assertEqual(self.calls(), [])

    def test_dry_run_sends_nothing_and_claims_nothing(self):
        d = self.task('CASE-X-OBS-Codex')
        self.assertEqual(R.run_task(d, FAKE, self.ex.parent, 60, dry_run=True), 'dry-run')
        self.assertEqual(self.calls(), []); self.assertFalse((d / R.CLAIM).exists())

    # --- the request must name this folder (R3) ---
    def test_r3_header_case_task_and_to_must_match_the_folder(self):
        def bad(c, t):
            return {'TASK of another folder': packet(c + '-SYN', c), 'CASE of another folder': packet(t, 'CASE-Q'),
                    'TO someone else': packet(t, c, to='GPT'),
                    'two TASK in the header': f'[AI TEAM STUDIO｜観測依頼]  CASE: {c}  TASK: {t}  TASK: {t}  TO: Codex\n本文\n',
                    'no TO in the header': f'[AI TEAM STUDIO｜観測依頼]  CASE: {c}  TASK: {t}\nTO: Codex\n',
                    'header not on the first line': f'本文だけ\nCASE: {c}  TASK: {t}  TO: Codex\n'}
        for i, why in enumerate(bad('C', 'T')):
            with self.subTest(why=why):
                c = f'C{i}'; t = c + '-OBS-Codex'
                d = self.task(t, text=bad(c, t)[why], case=c)
                self.assertEqual(self.run_(d), 'failed', why)
                self.assertEqual(self.claim(d)['state'], 'failed')
        self.assertEqual(self.calls(), [])

    def test_r3_the_reply_template_in_the_body_does_not_count(self):
        d = self.task('CASE-X-OBS-Codex')                       # packet() repeats TASK in the body
        self.assertEqual(self.run_(d), 'published')

    def test_r3_task_option_accepts_only_a_folder_inside_the_exchange(self):
        inside = self.task('CASE-X-OBS-Codex')
        outside = Path(self.tmp.name) / 'elsewhere' / 'CASE-X' / 'CASE-X-OBS-Codex'; outside.mkdir(parents=True)
        (outside / 'to-Codex.md').write_bytes(packet('CASE-X-OBS-Codex').encode('utf-8'))
        self.assertIsNone(R.task_problem(inside, self.ex))
        self.assertIsNotNone(R.task_problem(outside, self.ex))
        self.assertIsNotNone(R.task_problem(self.ex / 'CASE-X', self.ex))                   # not <CASE>/<TASK>
        with self.assertRaises(SystemExit):
            R.main(['--codex', str(HERE / 'fake_codex.py'), '--exchange', str(self.ex), '--task', str(outside)])
        self.assertEqual(self.calls(), []); self.assertFalse((outside / R.CLAIM).exists())

    def test_r3_a_link_on_the_way_is_refused(self):
        inside = self.task('CASE-X-OBS-Codex')
        real = Path.is_symlink
        with mock.patch.object(Path, 'is_symlink', lambda p: p.name == 'CASE-X' or real(p)):
            self.assertIsNotNone(R.task_problem(inside, self.ex))

    def test_execution_tasks_are_refused_by_task_option(self):
        d = self.task('CASE-X-NEXT')
        with self.assertRaises(SystemExit):
            R.main(['--codex', str(HERE / 'fake_codex.py'), '--exchange', str(self.ex), '--task', str(d)])
        self.assertEqual(self.calls(), [])

    # --- effort (R4) and the record of the run (R5) ---
    def test_r4_effort_is_passed_and_recorded_only_for_known_values(self):
        d = self.task('CASE-X-OBS-Codex')
        R.run_task(d, FAKE, self.ex.parent, 60, effort='medium')
        (c,) = self.calls()
        self.assertIn('model_reasoning_effort="medium"', c['args'])
        cond = self.claim(d)['condition']
        self.assertEqual(cond['effort'], 'medium'); self.assertEqual(cond['effortReported'], 'medium')
        d2 = self.task('CASE-Y-OBS-Codex', case='CASE-Y'); self.run_(d2)
        self.assertFalse(any('model_reasoning_effort' in x for x in self.calls()[1]['args']))   # no effort: Codex's own default
        for ok in R.EFFORTS:
            self.assertIsNone(R.effort_problem(ok))
        d3 = self.task('CASE-Z-OBS-Codex', case='CASE-Z')
        for bad in ('High', 'high"', 'a=b', '', 'ultra', 'none'):
            self.assertIsNotNone(R.effort_problem(bad), bad)
            with self.assertRaises(SystemExit):
                R.main(['--codex', str(HERE / 'fake_codex.py'), '--exchange', str(self.ex), '--effort', bad, '--task', str(d3)])
        self.assertEqual(len(self.calls()), 2); self.assertFalse((d3 / R.CLAIM).exists())

    def test_r5_the_log_keeps_the_header_and_never_the_request(self):
        d = self.task('CASE-X-OBS-Codex'); self.run_(d, effort='high')
        log = self.log(d)
        for line in ('model: fake-model', 'reasoning effort: high', 'sandbox: read-only', 'approval: never'):
            self.assertIn(line, log)
        self.assertIn('exit 0', log); self.assertIn('exec --ephemeral', log)
        self.assertNotIn('日本語の問い', log); self.assertNotIn(R.PREAMBLE.splitlines()[0], log)   # the request is not logged
        self.assertNotIn('session id', log)

    def test_r3b_a_failure_logs_only_kinds_of_errors_never_their_text(self):
        # 3-1-r3: an ERROR line may carry anything (a token, a path, command output): only its kind is kept.
        self.fake('fail')
        d = self.task('CASE-X-OBS-Codex'); self.run_(d)
        log, claim = self.log(d), json.dumps(self.claim(d), ensure_ascii=False)
        for leak in ('fake failure', 'SECRET-TOKEN-123', 'C:\\private', '日本語の問い'):
            self.assertNotIn(leak, log, leak); self.assertNotIn(leak, claim, leak)
        self.assertIn('エラーの種類: auth, other', log); self.assertIn('ERROR 行 2 件', log)

    def test_r3b_error_kinds(self):
        self.assertEqual(R.error_kinds('ERROR: 401 Unauthorized\nerror: stream disconnected (connection reset)\nERROR: rate limit 429\n'
                                       'ERROR: sandbox denied\nERROR: model not found\nERROR: something else\nINFO: fine\n'),
                         (['auth', 'network', 'rate', 'sandbox', 'model', 'other'], 6))
        self.assertEqual(R.error_kinds('no errors here\n'), ([], 0))

    # --- 3-1-r3: --once / --watch check the place too ---
    def test_r3a_once_and_watch_skip_folders_reached_through_a_link(self):
        ok = self.task('CASE-X-OBS-Codex'); linked = self.task('CASE-L-OBS-Codex', case='CASE-L')
        real = Path.is_symlink
        with mock.patch.object(Path, 'is_symlink', lambda p: p.name == 'CASE-L' or real(p)):
            self.assertEqual(R.waiting_tasks(self.ex), [ok])
            R.main(['--codex', str(HERE / 'fake_codex.py'), '--exchange', str(self.ex), '--repo', self.tmp.name, '--once'])
        self.assertEqual(len(self.calls()), 1)
        self.assertTrue((ok / 'from-Codex.md').exists()); self.assertFalse((linked / R.CLAIM).exists())

    # --- 3-1-r3: after the reply is published, a failing record never turns it into "failed" (S1-2) ---
    def test_r3c_a_failing_log_after_publishing_keeps_the_done_claim(self):
        d = self.task('CASE-X-OBS-Codex')
        real = R.log
        def flaky(task_dir, text):
            if 'を書きました' in text:
                raise OSError('disk full')
            return real(task_dir, text)
        with mock.patch.object(R, 'log', side_effect=flaky):
            self.assertEqual(self.run_(d), 'published')
        c = self.claim(d)
        self.assertEqual(c['state'], 'done'); self.assertEqual(c['replySha256'], sha((d / 'from-Codex.md').read_bytes()))
        self.assertIn('OSError', c['recordError'])

    def test_r3c_a_failing_done_claim_is_written_again_with_the_error(self):
        d = self.task('CASE-X-OBS-Codex')
        real, seen = R.write_claim, []
        def flaky(task_dir, state, **extra):
            seen.append(state)
            if state == 'done' and seen.count('done') == 1:
                raise PermissionError('claim locked')
            return real(task_dir, state, **extra)
        with mock.patch.object(R, 'write_claim', side_effect=flaky):
            self.assertEqual(self.run_(d), 'published')
        c = self.claim(d)
        self.assertEqual(c['state'], 'done'); self.assertIn('PermissionError', c['recordError'])
        self.assertTrue((d / 'from-Codex.md').exists()); self.assertNotIn('failed', seen)

    def test_r3c_a_temporary_file_that_cannot_be_removed_after_publishing_is_noted_not_failed(self):
        d = self.task('CASE-X-OBS-Codex')
        with mock.patch.object(R.os, 'unlink', side_effect=PermissionError('in use')):
            self.assertEqual(self.run_(d), 'published')
        c = self.claim(d)
        self.assertEqual(c['state'], 'done'); self.assertTrue(c['tmpLeft'])
        self.assertEqual((d / 'from-Codex.md').read_bytes(), self.reply.encode('utf-8'))

    def test_r3c_the_conflict_record_survives_a_failing_log_too(self):
        self.fake('person-first')
        d = self.task('CASE-X-OBS-Codex')
        real = R.log
        with mock.patch.object(R, 'log', side_effect=lambda t, x: (_ for _ in ()).throw(OSError('x')) if '置きました' in x else real(t, x)):
            self.assertEqual(self.run_(d), 'conflict')
        c = self.claim(d)
        self.assertEqual(c['state'], 'conflict'); self.assertEqual(c['saved'], R.CONFLICT); self.assertIn('recordError', c)

    def test_r3d_a_failing_log_keeps_the_original_reason_of_a_failed_run(self):
        # The reason Codex failed (exit code) must survive a log that cannot be written; the log error is added beside it.
        self.fake('fail')
        d = self.task('CASE-X-OBS-Codex')
        real = R.log
        with mock.patch.object(R, 'log', side_effect=lambda t, x: (_ for _ in ()).throw(OSError('log locked')) if x.startswith('失敗: ') else real(t, x)):
            self.assertEqual(self.run_(d), 'failed')
        c = self.claim(d)
        self.assertEqual(c['state'], 'failed'); self.assertEqual(c['reason'], '終了コード 3')
        self.assertIn('OSError', c['recordError']); self.assertIn('condition', c); self.assertIsInstance(c['seconds'], int)
        self.assertFalse((d / R.TMP).exists()); self.assertFalse((d / 'from-Codex.md').exists())

    def test_r3c_the_error_handler_itself_never_raises(self):
        d = self.task('CASE-X-OBS-Codex')
        with mock.patch.object(R.os, 'link', side_effect=OSError('no links here')), \
             mock.patch.object(R, 'write_claim', side_effect=OSError('claim locked')), \
             mock.patch.object(R, 'log', side_effect=OSError('log locked')):
            self.assertEqual(self.run_(d), 'failed')          # returns instead of raising
        self.assertFalse((d / 'from-Codex.md').exists())

    # --- environment (R6) ---
    def test_r6_codex_gets_only_the_allowed_environment(self):
        d = self.task('CASE-X-OBS-Codex')
        with mock.patch.dict(os.environ, {'GEMINI_API_KEY': 'dummy', 'OPENAI_API_KEY': 'dummy', 'ATS_CANARY': 'dummy'}):
            self.run_(d)
        env = {k.upper() for k in self.calls()[0]['env']}
        for secret in ('GEMINI_API_KEY', 'OPENAI_API_KEY', 'ATS_CANARY'):
            self.assertNotIn(secret, env)
        self.assertIn('SYSTEMROOT', env); self.assertIn('USERPROFILE', env)
        # Python itself may add a few variables to its own environment (for example on Windows); none of them is a secret.
        extra = env - {k.upper() for k in R.ENV_ALLOW}
        self.assertFalse({k for k in extra if 'KEY' in k or 'TOKEN' in k or 'SECRET' in k}, extra)


if __name__ == '__main__':
    unittest.main()
