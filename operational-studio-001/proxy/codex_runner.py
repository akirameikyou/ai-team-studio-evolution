#!/usr/bin/env python3
"""AI Team Studio — Codex runner (stage 3-1-r2: the relay shows its state; a person or Claude still starts it).

What it does, per task folder exchange/<CASE>/<TASK>/:
  1. picks folders that have to-Codex.md, no from-Codex.md and no claim file .codex-run
     (only observation and synthesis tasks; execution tasks stay manual)
  2. claims the folder by creating .codex-run exclusively (no double runs)
  3. checks that the request's header line names this folder (CASE, TASK) and Codex (TO)
  4. runs `codex exec` read-only with the request on stdin and a minimal environment; Codex writes no files
  5. the runner writes the reply to a temporary file and links it to from-Codex.md (a link never replaces an existing
     file), so a reply a person wrote first is kept; the runner's reply then goes to from-Codex.runner[-n].md.
The claim stays after the run: state done / failed / conflict, the SHA-256 of the published bytes and the conditions
of the run. The relay returns it with GET /result (read only), so the Studio can show the state and record the
conditions only when the reply it collected is the one the runner published.

Run:  python codex_runner.py --dry-run              list what would run, send nothing
      python codex_runner.py --task <task folder>   run one task (recommended)
      python codex_runner.py --once                 run every waiting task once (old unanswered requests too)
      python codex_runner.py --watch 30             look every 30 s; stops when exchange/_runner_STOP exists
Sign-in: Codex's own (codex login). The runner never reads auth.json or proxy/.env.
"""
import argparse, glob, hashlib, json, os, re, subprocess, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_EXCHANGE = HERE / 'exchange'
DEFAULT_REPO = HERE.parent.parent          # the repository root: Codex reads the code it is asked about from here
RUNNER_VERSION = 'codex_runner 0.5'
PREAMBLE_VERSION = 'P1'                    # change this name whenever PREAMBLE changes (it is recorded as a condition)
PREAMBLE_IMPL_VERSION = 'IMPL-P1'
AI = 'Codex'
CLAIM, LOG = '.codex-run', 'codex-run.log'
TMP = '.from-Codex.md.runner.tmp'       # not the name Codex Desktop is asked to use, so the two never share a file
CONFLICT = 'from-Codex.runner.md'       # where the reply goes when a person wrote from-Codex.md first (then -2, -3 …)
MAX_CONFLICT = 99
STOP = '_runner_STOP'
# Observation and synthesis are batch eligible; implementation is eligible only through --task --write.
AUTO_TASK = re.compile(r'-(OBS-Codex|SYN|IMPL-Codex)(-\d+)?$')
IMPL_TASK = re.compile(r'-IMPL-Codex(?:-\d+)?$')
AUTO_BATCH_TASK = re.compile(r'-(OBS-Codex|SYN)(-\d+)?$')
# R4: the reasoning levels the current Codex models list (models_cache.json supported_reasoning_levels).
EFFORTS = ('low', 'medium', 'high', 'xhigh', 'max')
# R6: Codex gets only these variables. Keys and tokens loaded by whoever started the runner (the relay's .env, for
# example) never reach it. Sign-in is found through USERPROFILE / CODEX_HOME.
ENV_ALLOW = ('SYSTEMROOT', 'WINDIR', 'SYSTEMDRIVE', 'COMSPEC', 'PATH', 'PATHEXT', 'TEMP', 'TMP', 'USERPROFILE',
             'LOCALAPPDATA', 'APPDATA', 'HOMEDRIVE', 'HOMEPATH', 'COMPUTERNAME', 'USERNAME', 'USERDOMAIN',
             'PROGRAMDATA', 'PROGRAMFILES', 'PROGRAMFILES(X86)', 'COMMONPROGRAMFILES', 'ALLUSERSPROFILE', 'OS',
             'PROCESSOR_ARCHITECTURE', 'NUMBER_OF_PROCESSORS', 'PSMODULEPATH', 'CODEX_HOME')
# R5: the only stderr lines kept (from Codex's header). Everything after the header repeats the request: never logged.
HEADER_KEYS = ('model', 'reasoning effort', 'sandbox', 'approval')
HEADER_FIELD = re.compile(r'\b(CASE|TASK|TO):\s*(\S+)')
PREAMBLE = ('以下は AI Team Studio からの依頼文です（実行係 codex_runner.py が渡しています）。\n'
            'ファイルは作成・変更・削除しないでください。依頼文が「結果ファイルを書く」よう求めていても、書かないでください。\n'
            '返事は、依頼文が求める本文（最後のブロックを含む）だけを、最後のメッセージとして返してください。'
            'その本文を実行係が from-Codex.md として保存します。\n'
            'proxy\\.env と proxy\\exchange\\_snapshots の中身は開かないでください。\n'
            '\n===== 依頼文ここから =====\n')
PREAMBLE_IMPL = ('以下は AI Team Studio からの実装依頼文です（実行係 codex_runner.py が渡しています）。\n'
            '変更してよいのは作業フォルダ（-C）の中で、依頼文が指定した範囲だけです。\n'
            'commit・push・ブランチの削除・履歴の書き換えをしないでください（依頼文がブランチ作成を求めた場合の作成は可）。\n'
            'proxy\\.env・proxy\\exchange\\_snapshots・認証情報を開かないでください。\n'
            '報告ファイルは書かず、依頼文が求める報告の全文（最後のブロックを含む）を最後のメッセージとして返してください。'
            '実行係が from-Codex.md に保存します。\n'
            '\n===== 依頼文ここから =====\n')
POSTAMBLE = '\n===== 依頼文ここまで =====\n'


def now():
    return time.strftime('%Y-%m-%dT%H:%M:%S')


def sha256(b):
    return hashlib.sha256(b).hexdigest()


def find_codex(given=None):
    """--codex, then CODEX_EXE, then the newest %LOCALAPPDATA%\\OpenAI\\Codex\\bin\\*\\codex.exe (the folder name changes on updates)."""
    p = given or os.environ.get('CODEX_EXE')
    if not p:
        hits = glob.glob(os.path.join(os.environ.get('LOCALAPPDATA', ''), 'OpenAI', 'Codex', 'bin', '*', 'codex.exe'))
        p = max(hits, key=os.path.getmtime) if hits else None
    if not p or not os.path.isfile(p):
        raise SystemExit('codex.exe が見つかりません（--codex か環境変数 CODEX_EXE で指定してください）')
    return [sys.executable, p] if p.endswith('.py') else [p]   # .py = a stand-in used by the tests


def codex_env():
    """R6: the environment Codex is started with (an allow-list, compared without case as Windows does)."""
    allow = {k.upper() for k in ENV_ALLOW}
    return {k: v for k, v in os.environ.items() if k.upper() in allow}


_VERSIONS = {}


def codex_version(codex):
    key = tuple(codex)
    if key not in _VERSIONS:
        try:
            p = subprocess.run(codex + ['--version'], capture_output=True, timeout=30, env=codex_env())
            _VERSIONS[key] = p.stdout.decode('utf-8', errors='replace').strip()[:80] or 'unknown'
        except (OSError, subprocess.TimeoutExpired):
            _VERSIONS[key] = 'unknown'
    return _VERSIONS[key]


def effort_problem(effort):
    return None if effort in EFFORTS else f'推論の強さは {"・".join(EFFORTS)} のどれかです（指定：{effort!r}）'


def codex_args(codex, repo, out, effort=None, sandbox='read-only'):
    extra = ['-c', f'model_reasoning_effort="{effort}"'] if effort else []   # effort is one of EFFORTS (R4)
    return codex + ['exec', '--ephemeral', '-s', sandbox, '-c', 'approval_policy="never"', *extra,
                    '-C', str(repo), '-o', str(out), '-']


def header_lines(stderr):
    """R5: Codex's header (between the first two '--------' lines), only the HEADER_KEYS lines."""
    lines, inside, seps = [], False, 0
    for line in stderr.splitlines():
        if line.strip() == '--------':
            seps += 1
            inside = seps == 1
            if seps >= 2:
                break
            continue
        if inside and line.split(':', 1)[0].strip() in HEADER_KEYS:
            lines.append(line.strip()[:200])
    return lines


# 3-1-r3: an error line may carry anything (a token, a path, command output), so only its kind is recorded.
ERROR_KINDS = (('auth', r'\b(401|403)\b|unauthori[sz]ed|login|auth'), ('network', r'time(d)?\s*out|connection|network|dns|disconnect'),
               ('rate', r'\b429\b|rate.?limit|quota'), ('sandbox', r'sandbox|permission denied|access is denied|アクセスが拒否'),
               ('model', r'\bmodel\b'))


def error_kinds(stderr):
    """On failure: (the kinds of the lines Codex marks as errors, in a fixed order; how many such lines). Never their text."""
    found, n = set(), 0
    for line in stderr.splitlines():
        if not re.match(r'^\s*(ERROR|error)\b', line):
            continue
        n += 1
        found.add(next((k for k, pat in ERROR_KINDS if re.search(pat, line, re.I)), 'other'))
    return [k for k in [k for k, _ in ERROR_KINDS] + ['other'] if k in found], n


def condition(codex, effort, header=(), sandbox='read-only', preamble=PREAMBLE_VERSION):
    """The conditions of this run, recorded with the reply (who carried it and how)."""
    h = dict(l.split(':', 1) for l in header)
    return {'route': 'runner', 'runner': RUNNER_VERSION, 'preamble': preamble, 'codex': codex_version(codex),
            'sandbox': sandbox, 'effort': effort or 'default',
            'modelReported': h.get('model', '').strip()[:100] or 'unknown',
            'effortReported': h.get('reasoning effort', '').strip()[:100] or 'unknown'}


def waiting_tasks(exchange):
    out = []
    for d in sorted(Path(exchange).glob('*/*')):
        if not d.is_dir() or d.parent.name.startswith('_') or not AUTO_BATCH_TASK.search(d.name):
            continue
        if (d / f'to-{AI}.md').is_file() and not (d / f'from-{AI}.md').exists() and not (d / CLAIM).exists() \
                and task_problem(d, exchange) is None:     # 3-1-r3: --once / --watch check the place as --task does
            out.append(d)
    return out


def task_problem(task_dir, exchange):
    """R3: None if task_dir is exchange/<CASE>/<TASK> itself, with no link on the way."""
    task_dir, exchange = Path(task_dir), Path(exchange)
    if any(p.is_symlink() for p in (task_dir, task_dir.parent)):
        return 'フォルダの途中にリンクがあります'
    try:
        real, ex = task_dir.resolve(strict=True), exchange.resolve(strict=True)
    except OSError:
        return 'フォルダがありません'
    if os.path.normcase(os.path.abspath(task_dir)) != os.path.normcase(str(real)):
        return 'フォルダの途中にリンクがあります'
    if real.parent.parent != ex:
        return f'受け渡しフォルダ（{ex}）の中の <CASE>/<TASK> ではありません'
    return None


def request_problem(task_dir, packet):
    """R3: None if the request's header (its first non-empty line) names this folder's CASE and TASK, and Codex, once
    each. The body is not read: the reply template at its end repeats TASK, as it should."""
    text = packet.decode('utf-8', errors='replace')
    head = next((l for l in text.splitlines() if l.strip()), '')
    found = HEADER_FIELD.findall(head)
    keys = [k for k, _ in found]
    if sorted(keys) != ['CASE', 'TASK', 'TO']:
        return '依頼文の見出し（1行目）に CASE・TASK・TO が1つずつありません'
    f = dict(found)
    if f['TASK'] != task_dir.name:
        return f'依頼文の TASK（{f["TASK"]}）がフォルダ名（{task_dir.name}）と合いません'
    if f['CASE'] != task_dir.parent.name:
        return f'依頼文の CASE（{f["CASE"]}）が Case のフォルダ名（{task_dir.parent.name}）と合いません'
    if f['TO'] != AI:
        return f'依頼文の宛先（{f["TO"]}）が {AI} ではありません'
    return None


def write_claim(task_dir, state, **extra):
    (task_dir / CLAIM).write_text(json.dumps({'state': state, 'at': now(), **extra}, ensure_ascii=False), encoding='utf-8')


def log(task_dir, text):
    with open(task_dir / LOG, 'a', encoding='utf-8') as f:
        f.write(f'[{now()}] {text}\n')


def publish(tmp, dest):
    """R1: give tmp the name dest only if dest does not exist yet (a hard link never replaces a file), then drop tmp."""
    os.link(tmp, dest)
    try:
        os.unlink(tmp)
        return True
    except OSError:      # 3-1-r3: the reply is published; a temporary file left behind is noted, not a failure
        return False


def record_outcome(task_dir, state, message, **fields):
    """3-1-r3 (S1-2): the claim must keep the outcome of a run (done / conflict / failed, with its reason) even if
    writing the claim or the log fails. Such a failure is added to the claim as recordError (one more try); it never
    changes the state or replaces the reason."""
    try:
        write_claim(task_dir, state, **fields)
        log(task_dir, message)
    except Exception as e:
        err = f'{type(e).__name__}: {str(e)[:150]}'
        for step in (lambda: write_claim(task_dir, state, recordError=err, **fields),
                     lambda: log(task_dir, f'{message}（記録の一部に失敗: {err}）')):
            try:
                step()
            except Exception:
                pass


def git_snapshot(repo):
    """Return HEAD and porcelain paths for a git worktree, or raise with a short diagnostic."""
    head = subprocess.run(['git', '-C', str(repo), 'rev-parse', 'HEAD'], capture_output=True, timeout=30)
    if head.returncode:
        raise RuntimeError('git rev-parse HEAD に失敗しました')
    status = subprocess.run(['git', '-C', str(repo), 'status', '--porcelain'], capture_output=True, timeout=30)
    if status.returncode:
        raise RuntimeError('git status --porcelain に失敗しました')
    return {'head': head.stdout.decode('utf-8', errors='replace').strip(),
            'status': [line.rstrip() for line in status.stdout.decode('utf-8', errors='replace').splitlines() if line]}


def status_paths(lines):
    """Extract filenames from porcelain records while retaining status codes in the snapshots."""
    return [line[3:] if len(line) >= 3 and line[2] == ' ' else line for line in lines]


def run_task(task_dir, codex, repo, timeout, dry_run=False, effort=None, write=False):
    """Returns 'published' | 'conflict' | 'failed' | 'skipped' | 'dry-run'."""
    task_dir = Path(task_dir)
    to_file, from_file, tmp = task_dir / f'to-{AI}.md', task_dir / f'from-{AI}.md', task_dir / TMP
    if not to_file.is_file() or from_file.exists():
        return 'skipped'
    packet = to_file.read_bytes()
    problem = request_problem(task_dir, packet)
    is_impl = bool(IMPL_TASK.search(task_dir.name))
    if is_impl and not write:
        if dry_run:
            print(f'{task_dir}\n  実装依頼は実行しません（--task と --write が必要）')
            return 'dry-run'
        print('実装依頼は --task と --write の両方が必要です。送信しません。')
        return 'skipped'
    sandbox = 'workspace-write' if is_impl else 'read-only'
    preamble_version = PREAMBLE_IMPL_VERSION if is_impl else PREAMBLE_VERSION
    args = codex_args(codex, repo, tmp, effort, sandbox)
    if dry_run:
        print(f'{task_dir}\n  依頼 {len(packet)} バイト / {problem or "見出しが一致"}\n  {subprocess.list2cmdline(args)}')
        return 'dry-run'
    try:   # exclusive claim: a second runner (or a second click) on the same folder stops here
        fd = os.open(task_dir / CLAIM, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
    except FileExistsError:
        return 'skipped'
    since = now()
    if problem:
        write_claim(task_dir, 'failed', since=since, reason=problem); log(task_dir, '送らずに止めました: ' + problem)
        return 'failed'
    before = None
    if is_impl:
        try:
            before = git_snapshot(repo)
        except (OSError, subprocess.TimeoutExpired, RuntimeError) as e:
            reason = f'git の状態を記録できなかったため送信しません: {type(e).__name__}: {str(e)[:120]}'
            record_outcome(task_dir, 'failed', reason, since=since, reason=reason, gitRecord='記録できなかった')
            return 'failed'
    try:
        return _run_claimed(task_dir, codex, args, packet, since, timeout, effort, from_file, tmp,
                            is_impl=is_impl, repo=repo, before=before, preamble_version=preamble_version)
    except Exception as e:   # S1-4: whatever happens, the claim does not stay "running" and no temporary file is left
        reason = f'{type(e).__name__}: {str(e)[:150]}'
        # 3-1-r3: each step on its own, and none of them may raise (a failing handler would leave "running" behind)
        for step in (lambda: write_claim(task_dir, 'failed', since=since, reason=reason),
                     lambda: log(task_dir, '失敗（予期しないエラー）: ' + reason),
                     lambda: tmp.unlink() if tmp.exists() else None):
            try:
                step()
            except Exception:
                pass
        return 'failed'


def _run_claimed(task_dir, codex, args, packet, since, timeout, effort, from_file, tmp,
                 is_impl=False, repo=None, before=None, preamble_version=PREAMBLE_VERSION):
    sandbox = 'workspace-write' if is_impl else 'read-only'
    preamble = PREAMBLE_IMPL if is_impl else PREAMBLE
    cond = condition(codex, effort, sandbox=sandbox, preamble=preamble_version)
    fields = {'gitBefore': before} if is_impl else {}
    write_claim(task_dir, 'running', since=since, pid=os.getpid(), condition=cond, **fields)
    if tmp.exists():
        tmp.unlink()
    started, timed_out = time.time(), False
    try:
        p = subprocess.run(args, input=preamble.encode('utf-8') + packet + POSTAMBLE.encode('utf-8'),
                           capture_output=True, timeout=timeout, env=codex_env())
        code, err = p.returncode, p.stderr.decode('utf-8', errors='replace')
    except subprocess.TimeoutExpired as e:
        code, err, timed_out = None, (e.stderr or b'').decode('utf-8', errors='replace'), True
    secs = int(time.time() - started)
    after = None
    git_fields = {}
    if is_impl:
        try:
            after = git_snapshot(repo)
            git_fields = {'gitBefore': before, 'gitAfter': after,
                          'changedFiles': sorted(set(status_paths(after['status'])))}
            if after['head'] != before['head']:
                git_fields['gitWarning'] = 'HEAD が実行前後で変化しました'
                log(task_dir, f"警告: HEAD が変化しました（実行前 {before['head']} / 実行後 {after['head']}）")
            log(task_dir, f"git 実行前 HEAD {before['head']} / status {json.dumps(before['status'], ensure_ascii=False)}\n"
                          f"  実行後 HEAD {after['head']} / status {json.dumps(after['status'], ensure_ascii=False)}\n"
                          f"  変更ファイル {json.dumps(git_fields['changedFiles'], ensure_ascii=False)}")
        except (OSError, subprocess.TimeoutExpired, RuntimeError) as e:
            git_fields = {'gitRecord': f'記録できなかった: {type(e).__name__}: {str(e)[:120]}'}
            log(task_dir, 'git 状態を記録できなかった')
    header = header_lines(err)
    cond = condition(codex, effort, header, sandbox=sandbox, preamble=preamble_version)
    log(task_dir, f'実行 {subprocess.list2cmdline(args[len(codex):])}\n'
                  f'  開始 {since} / {secs} 秒 / exit {code if code is not None else "-"}{" / 時間切れ" if timed_out else ""}\n'
                  + ''.join(f'  {l}\n' for l in header))
    reply = tmp.read_bytes() if tmp.exists() else b''
    if code != 0 or not reply.strip():
        reason = f'時間切れ（{timeout} 秒）' if timed_out else (f'終了コード {code}' if code != 0 else '返事が空')
        kinds, n = error_kinds(err)
        record_outcome(task_dir, 'failed', f'失敗: {reason}（{secs} 秒）'
                       + (f'\n  エラーの種類: {", ".join(kinds)}（ERROR 行 {n} 件。内容は記録しない）' if n else ''),
                       since=since, reason=reason, seconds=secs, condition=cond, **git_fields)
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        return 'failed'
    digest = sha256(reply)
    note = '' if re.search(r'TASK:\s*' + re.escape(task_dir.name) + r'\s', reply.decode('utf-8', errors='replace')) \
        else '（注意: 返事に TASK の一致する行がありません。判定はスタジオに任せます）'
    try:
        removed = publish(tmp, from_file)   # fails if from-Codex.md appeared meanwhile: never overwrite a person's reply
    except FileExistsError:
        for k in range(1, MAX_CONFLICT + 1):
            name = CONFLICT if k == 1 else f'from-{AI}.runner-{k}.md'
            try:
                removed = publish(tmp, task_dir / name)
                break
            except FileExistsError:
                continue
        else:
            raise OSError(f'退避先を作れません（{CONFLICT} から -{MAX_CONFLICT} まで使用中）')
        left = {} if removed else {'tmpLeft': True}
        record_outcome(task_dir, 'conflict', f'from-{AI}.md が先にあったので、返事は {name} に置きました（{secs} 秒）',
                         since=since, seconds=secs, saved=name, savedSha256=digest, condition=cond, **git_fields,
                         reason=f'from-{AI}.md が先にありました。実行係の返事は {name} にあります', **left)
        return 'conflict'
    # The claim stays as a record of the run (done, the hash of what was published, the conditions).
    left = {} if removed else {'tmpLeft': True}
    record_outcome(task_dir, 'done', f'from-{AI}.md を書きました（{len(reply)} バイト, {secs} 秒）{note}'
                     + ('' if removed else f'（一時ファイル {TMP} を消せませんでした）'),
                     since=since, seconds=secs, replySha256=digest, condition=cond, **git_fields, **left)
    return 'published'


def main(argv=None):
    ap = argparse.ArgumentParser(description='AI Team Studio — Codex runner')
    ap.add_argument('--exchange', default=str(DEFAULT_EXCHANGE))
    ap.add_argument('--repo', help='Codex の作業フォルダ（-C）。実装依頼では必須')
    ap.add_argument('--write', action='store_true', help='--task の実装依頼を workspace-write で実行')
    ap.add_argument('--codex', help='codex.exe のパス')
    ap.add_argument('--timeout', type=int, default=1800)
    ap.add_argument('--effort', help='推論の強さ（' + '・'.join(EFFORTS) + '）。省略すると Codex の既定')
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--dry-run', action='store_true')
    g.add_argument('--task')
    g.add_argument('--once', action='store_true')
    g.add_argument('--watch', type=int, metavar='SECONDS')
    a = ap.parse_args(argv)
    if a.effort is not None and effort_problem(a.effort):
        raise SystemExit(effort_problem(a.effort))
    repo = a.repo or str(DEFAULT_REPO)
    if a.task:
        task = Path(a.task)
        if not AUTO_TASK.search(task.name):
            raise SystemExit(f'{task.name} は自動の対象外です（観測 -OBS-Codex、統合 -SYN、実装 -IMPL-Codex）')
        is_impl = bool(IMPL_TASK.search(task.name))
        if is_impl and not a.write:
            raise SystemExit('実装依頼は --write が必要です。送信しません。')
        if is_impl and a.repo is None:
            raise SystemExit('実装依頼は --repo の明示が必要です。送信しません。')
        problem = task_problem(task, a.exchange)
        if problem:
            raise SystemExit(f'{task} は実行しません：{problem}')
        codex = find_codex(a.codex)
        print(run_task(task, codex, repo, a.timeout, effort=a.effort, write=a.write))
        return
    codex = find_codex(a.codex)
    while True:
        tasks = waiting_tasks(a.exchange)
        if a.dry_run:
            # Preview implementation requests as well, while keeping them out of --once / --watch execution.
            for d in sorted(Path(a.exchange).glob('*/*')):
                if d.is_dir() and not d.parent.name.startswith('_') and IMPL_TASK.search(d.name) \
                        and (d / f'to-{AI}.md').is_file() and not (d / f'from-{AI}.md').exists() \
                        and not (d / CLAIM).exists() and task_problem(d, a.exchange) is None:
                    tasks.append(d)
        for d in tasks:
            if IMPL_TASK.search(d.name):
                print(f'{d.name}\n  実装依頼は --task と --write が必要です。送信しません。', flush=True)
                continue
            print(d.name, run_task(d, codex, repo, a.timeout, dry_run=a.dry_run, effort=a.effort), flush=True)
        if not a.watch or (Path(a.exchange) / STOP).exists():
            return
        time.sleep(a.watch)


if __name__ == '__main__':
    main()
