#!/usr/bin/env python3
"""AI Team Studio — Codex runner (stage 3-1: the relay shows its state; a person or Claude still starts it).

What it does, per task folder exchange/<CASE>/<TASK>/:
  1. picks folders that have to-Codex.md, no from-Codex.md and no claim file .codex-run
     (only observation and synthesis tasks; execution tasks stay manual)
  2. claims the folder by creating .codex-run exclusively (no double runs)
  3. runs `codex exec` read-only with the request on stdin; Codex writes no files
  4. the runner writes the reply to a temporary file and renames it to from-Codex.md.
     os.rename never replaces an existing file on Windows, so a reply a person wrote first is kept.
The claim stays after the run: state done / failed / conflict, with the conditions of the run. The relay returns it
with GET /result (read only), so the Studio can show the state and record the conditions with the reply.

Run:  python codex_runner.py --dry-run              list what would run, send nothing
      python codex_runner.py --task <task folder>   run one task (recommended)
      python codex_runner.py --once                 run every waiting task once (old unanswered requests too)
      python codex_runner.py --watch 30             look every 30 s; stops when exchange/_runner_STOP exists
Sign-in: Codex's own (codex login). The runner never reads auth.json or proxy/.env.
"""
import argparse, glob, json, os, re, subprocess, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_EXCHANGE = HERE / 'exchange'
DEFAULT_REPO = HERE.parent.parent          # the repository root: Codex reads the code it is asked about from here
RUNNER_VERSION = 'codex_runner 0.2'
PREAMBLE_VERSION = 'P1'                    # change this name whenever PREAMBLE changes (it is recorded as a condition)
AI = 'Codex'
CLAIM, LOG = '.codex-run', 'codex-run.log'
TMP = '.from-Codex.md.runner.tmp'       # not the name Codex Desktop is asked to use, so the two never share a file
CONFLICT = 'from-Codex.runner.md'       # where the reply goes when a person wrote from-Codex.md first
STOP = '_runner_STOP'
# Observation (<CASE>-OBS-Codex[-n]) and synthesis (<CASE>-SYN[-n]) only. NEXT / IMPL need writes: manual for now.
AUTO_TASK = re.compile(r'-(OBS-Codex|SYN)(-\d+)?$')
PREAMBLE = ('以下は AI Team Studio からの依頼文です（実行係 codex_runner.py が渡しています）。\n'
            'ファイルは作成・変更・削除しないでください。依頼文が「結果ファイルを書く」よう求めていても、書かないでください。\n'
            '返事は、依頼文が求める本文（最後のブロックを含む）だけを、最後のメッセージとして返してください。'
            'その本文を実行係が from-Codex.md として保存します。\n'
            'proxy\\.env と proxy\\exchange\\_snapshots の中身は開かないでください。\n'
            '\n===== 依頼文ここから =====\n')
POSTAMBLE = '\n===== 依頼文ここまで =====\n'


def now():
    return time.strftime('%Y-%m-%dT%H:%M:%S')


def find_codex(given=None):
    """--codex, then CODEX_EXE, then the newest %LOCALAPPDATA%\\OpenAI\\Codex\\bin\\*\\codex.exe (the folder name changes on updates)."""
    p = given or os.environ.get('CODEX_EXE')
    if not p:
        hits = glob.glob(os.path.join(os.environ.get('LOCALAPPDATA', ''), 'OpenAI', 'Codex', 'bin', '*', 'codex.exe'))
        p = max(hits, key=os.path.getmtime) if hits else None
    if not p or not os.path.isfile(p):
        raise SystemExit('codex.exe が見つかりません（--codex か環境変数 CODEX_EXE で指定してください）')
    return [sys.executable, p] if p.endswith('.py') else [p]   # .py = a stand-in used by the tests


_VERSIONS = {}


def codex_version(codex):
    key = tuple(codex)
    if key not in _VERSIONS:
        try:
            p = subprocess.run(codex + ['--version'], capture_output=True, timeout=30)
            _VERSIONS[key] = p.stdout.decode('utf-8', errors='replace').strip()[:80] or 'unknown'
        except (OSError, subprocess.TimeoutExpired):
            _VERSIONS[key] = 'unknown'
    return _VERSIONS[key]


def codex_args(codex, repo, out, effort=None):
    extra = ['-c', f'model_reasoning_effort="{effort}"'] if effort else []
    return codex + ['exec', '--ephemeral', '-s', 'read-only', '-c', 'approval_policy="never"', *extra,
                    '-C', str(repo), '-o', str(out), '-']


def condition(codex, effort):
    """The conditions of this run, recorded with the reply (who carried it and how)."""
    return {'route': 'runner', 'runner': RUNNER_VERSION, 'preamble': PREAMBLE_VERSION, 'codex': codex_version(codex),
            'sandbox': 'read-only', 'effort': effort or 'default'}


def waiting_tasks(exchange):
    out = []
    for d in sorted(Path(exchange).glob('*/*')):
        if not d.is_dir() or d.parent.name.startswith('_') or not AUTO_TASK.search(d.name):
            continue
        if (d / f'to-{AI}.md').is_file() and not (d / f'from-{AI}.md').exists() and not (d / CLAIM).exists():
            out.append(d)
    return out


def request_problem(task_dir, packet):
    """None if the request names this folder's task, else why it must not be sent (the agent instruction says the same)."""
    head = packet.decode('utf-8', errors='replace')[:2000]
    m = re.search(r'TASK:\s*(\S+)', head)
    if not m:
        return '依頼文に TASK がありません'
    if m.group(1) != task_dir.name:
        return f'依頼文の TASK（{m.group(1)}）がフォルダ名（{task_dir.name}）と合いません'
    return None


def write_claim(task_dir, state, **extra):
    (task_dir / CLAIM).write_text(json.dumps({'state': state, 'at': now(), **extra}, ensure_ascii=False), encoding='utf-8')


def log(task_dir, text):
    with open(task_dir / LOG, 'a', encoding='utf-8') as f:
        f.write(f'[{now()}] {text}\n')


def run_task(task_dir, codex, repo, timeout, dry_run=False, effort=None):
    """Returns 'published' | 'conflict' | 'failed' | 'skipped' | 'dry-run'."""
    task_dir = Path(task_dir)
    to_file, from_file, tmp = task_dir / f'to-{AI}.md', task_dir / f'from-{AI}.md', task_dir / TMP
    if not to_file.is_file() or from_file.exists():
        return 'skipped'
    packet = to_file.read_bytes()
    problem = request_problem(task_dir, packet)
    args = codex_args(codex, repo, tmp, effort)
    if dry_run:
        print(f'{task_dir}\n  依頼 {len(packet)} バイト / {problem or "TASK 一致"}\n  {subprocess.list2cmdline(args)}')
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
    cond = condition(codex, effort)
    write_claim(task_dir, 'running', since=since, pid=os.getpid(), condition=cond)
    if tmp.exists():
        tmp.unlink()
    started = time.time()
    try:
        p = subprocess.run(args, input=PREAMBLE.encode('utf-8') + packet + POSTAMBLE.encode('utf-8'),
                           capture_output=True, timeout=timeout)
        code, err = p.returncode, p.stderr.decode('utf-8', errors='replace')
    except subprocess.TimeoutExpired:
        code, err = None, f'時間切れ（{timeout} 秒）'
    secs = int(time.time() - started)
    tail = '\n'.join(err.splitlines()[-15:])
    reply = tmp.read_bytes() if tmp.exists() else b''
    if code != 0 or not reply.strip():
        reason = f'時間切れ（{timeout} 秒）' if code is None else (f'終了コード {code}' if code != 0 else '返事が空')
        write_claim(task_dir, 'failed', since=since, reason=reason, seconds=secs, condition=cond)
        log(task_dir, f'失敗: {reason}（{secs} 秒）\n{tail}')
        if tmp.exists():
            tmp.unlink()
        return 'failed'
    note = '' if re.search(r'TASK:\s*' + re.escape(task_dir.name) + r'\s', reply.decode('utf-8', errors='replace')) \
        else '（注意: 返事に TASK の一致する行がありません。判定はスタジオに任せます）'
    try:
        os.rename(tmp, from_file)          # fails if from-Codex.md appeared meanwhile: never overwrite a person's reply
    except FileExistsError:
        os.rename(tmp, task_dir / CONFLICT)
        write_claim(task_dir, 'conflict', since=since, seconds=secs, saved=CONFLICT, condition=cond,
                    reason=f'from-{AI}.md が先にありました。実行係の返事は {CONFLICT} にあります')
        log(task_dir, f'from-{AI}.md が先にあったので、返事は {CONFLICT} に置きました（{secs} 秒）')
        return 'conflict'
    # The claim stays as a record of the run (done + conditions). The runner skips the folder because from- exists.
    write_claim(task_dir, 'done', since=since, seconds=secs, condition=cond)
    log(task_dir, f'from-{AI}.md を書きました（{len(reply)} バイト, {secs} 秒）{note}')
    return 'published'


def main(argv=None):
    ap = argparse.ArgumentParser(description='AI Team Studio — Codex runner')
    ap.add_argument('--exchange', default=str(DEFAULT_EXCHANGE))
    ap.add_argument('--repo', default=str(DEFAULT_REPO), help='Codex の作業フォルダ（-C）')
    ap.add_argument('--codex', help='codex.exe のパス')
    ap.add_argument('--timeout', type=int, default=1800)
    ap.add_argument('--effort', help='推論の強さ（model_reasoning_effort）。省略すると Codex の既定')
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--dry-run', action='store_true')
    g.add_argument('--task')
    g.add_argument('--once', action='store_true')
    g.add_argument('--watch', type=int, metavar='SECONDS')
    a = ap.parse_args(argv)
    codex = find_codex(a.codex)
    if a.task:
        task = Path(a.task)
        if not AUTO_TASK.search(task.name):
            raise SystemExit(f'{task.name} は自動の対象外です（観測 -OBS-Codex と統合 -SYN だけ）')
        print(run_task(task, codex, a.repo, a.timeout, effort=a.effort))
        return
    while True:
        for d in waiting_tasks(a.exchange):
            print(d.name, run_task(d, codex, a.repo, a.timeout, dry_run=a.dry_run, effort=a.effort), flush=True)
        if not a.watch or (Path(a.exchange) / STOP).exists():
            return
        time.sleep(a.watch)


if __name__ == '__main__':
    main()
