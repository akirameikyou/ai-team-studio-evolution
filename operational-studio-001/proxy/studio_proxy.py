#!/usr/bin/env python3
"""AI Team Studio — local relay (standard library only).

Why it exists:
  * API keys must never be in the browser or in GitHub. This relay reads them from its own
    environment (or proxy/.env, which is git-ignored) and calls the APIs server-side.
  * Local agents (Claude Code, Codex) can take part without a human copying text:
    the relay writes   exchange/<CASE>/<TASK>/to-<AI>.md
    and the agent writes exchange/<CASE>/<TASK>/from-<AI>.md, which the Studio collects.

Binds to 127.0.0.1 only. CORS is limited to localhost and the project's GitHub Pages origin.

Run:  python studio_proxy.py            (port 8787)
      python studio_proxy.py --port 9000
"""
import argparse, hashlib, json, os, re, sys, tempfile, threading, time, urllib.error, urllib.parse, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXCHANGE = HERE / 'exchange'
ALLOWED_ORIGINS = re.compile(r'^(https?://(127\.0\.0\.1|localhost)(:\d+)?|https://akirameikyou\.github\.io)$')
MAX_FETCH = 50 * 1024 * 1024
SETTLE_SECONDS = 2  # a reply file changed this recently (inclusive) may still be being written by an agent
# 2-C: copies of the Studio record in <exchange>/_snapshots. One file per copy; nothing is overwritten or deleted.
MAX_SNAPSHOT = 10 * 1024 * 1024
SNAPSHOT_NAME = re.compile(r'^ats-store-\d{8}T\d{9}Z-r\d+(-\d+)?\.json$')
LOCAL_ORIGIN = re.compile(r'^https?://(127\.0\.0\.1|localhost)(:\d+)?$')
_SNAPSHOT_NAME_LOCK = threading.Lock()


def snapshots_dir():
    return EXCHANGE / '_snapshots'


def snapshot_files():
    """Regular snapshot files directly in the snapshot folder, newest name first. Symbolic links (and anything that
    resolves outside the folder) are never listed or read."""
    d = snapshots_dir()
    if not d.exists() or d.is_symlink():      # a snapshot folder that is itself a link is not used at all
        return []
    root = d.resolve()
    out = [p for p in d.glob('ats-store-*.json')
           if SNAPSHOT_NAME.match(p.name) and not p.is_symlink() and p.is_file() and p.resolve().parent == root]
    return sorted(out, key=lambda p: p.name, reverse=True)


def snapshot_problem(text):
    """None if text is a Studio record (the shape app.js load() accepts), else the reason."""
    if not isinstance(text, str):
        return 'store must be the record as JSON text'
    try:
        o = json.loads(text)
    except ValueError:
        return 'store is not JSON'
    if not isinstance(o, dict) or o.get('version') != 1 or not isinstance(o.get('cases'), dict):
        return 'store is not a Studio record (version 1 with cases)'
    return None


# 3-1: the state a runner (codex_runner.py) leaves in a task folder as .<ai>-run. Read only, and only these fields,
# so the Studio can show "running / failed" and record how a reply was produced. Nothing else in the file is passed on.
MAX_RUNNER_CLAIM = 4096
RUNNER_STATES = ('queued', 'running', 'done', 'failed', 'conflict')
RUNNER_TEXT_FIELDS = ('since', 'at', 'reason')
RUNNER_CONDITION_FIELDS = ('route', 'runner', 'preamble', 'codex', 'sandbox', 'effort', 'modelReported', 'effortReported')
SHA256_HEX = re.compile(r'^[0-9a-f]{64}$')


def runner_check(recorded, data):
    """3-1-r2 R2: does the reply file (its bytes, before any newline conversion) equal what the runner published?"""
    if not isinstance(recorded, str) or not SHA256_HEX.match(recorded):
        return 'unverifiable'
    return 'match' if hashlib.sha256(data).hexdigest() == recorded else 'mismatch'


def runner_state(task_dir, to):
    """None when there is no claim file; {'state': 'unknown'} when there is one that cannot be used as it is.
    A done claim also carries the recorded reply hash under '_replySha256', for /result to compare and drop."""
    p = task_dir / f'.{to.lower()}-run'
    if not p.exists() and not p.is_symlink():
        return None
    try:
        if p.is_symlink() or not p.is_file() or p.resolve().parent != task_dir.resolve() or p.stat().st_size > MAX_RUNNER_CLAIM:
            return {'state': 'unknown'}
        o = json.loads(p.read_bytes().decode('utf-8'))
    except (OSError, ValueError):
        return {'state': 'unknown'}
    if not isinstance(o, dict) or o.get('state') not in RUNNER_STATES:
        return {'state': 'unknown'}
    out = {'state': o['state']}
    for k in RUNNER_TEXT_FIELDS:
        if isinstance(o.get(k), str):
            out[k] = o[k][:300]
    if isinstance(o.get('seconds'), int) and not isinstance(o.get('seconds'), bool):
        out['seconds'] = o['seconds']
    cond = o.get('condition')
    if isinstance(cond, dict):
        out['condition'] = {k: cond[k][:100] for k in RUNNER_CONDITION_FIELDS if isinstance(cond.get(k), str)}
    if out['state'] == 'done':
        out['_replySha256'] = o.get('replySha256')
    return out


def load_env():
    p = HERE / '.env'
    if p.exists():
        for line in p.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def safe(part):
    s = re.sub(r'[^\w.-]+', '_', str(part))[:80]
    if not s or s in ('.', '..'):
        raise ValueError('bad path component')
    return s


_WRITE_LOCKS = {}
_WRITE_LOCKS_GUARD = threading.Lock()
REPLACE_ATTEMPTS, REPLACE_WAIT = 5, 0.2  # Windows: replacing a file another process holds open can fail for a moment


def _lock_for(path):
    with _WRITE_LOCKS_GUARD:
        return _WRITE_LOCKS.setdefault(str(path), threading.Lock())


def write_atomic(path, text):
    """Write text as UTF-8 bytes, exactly (no newline translation), and publish it only when complete:
    the bytes go to a temporary file with a unique name in the same folder and os.replace() gives it the
    final name. Writes to the same path are serialised; the last one to finish wins. If the replace keeps
    failing (PermissionError while a reader holds the file on Windows), the temporary file is removed and
    the error is raised, so the caller answers 500 and the old file stays as it was."""
    with _lock_for(path):
        fd, tmp = tempfile.mkstemp(prefix='.' + path.name + '.', suffix='.tmp', dir=path.parent)
        try:
            with os.fdopen(fd, 'wb') as f:
                f.write(text.encode('utf-8'))
                f.flush()
                os.fsync(f.fileno())
            for attempt in range(REPLACE_ATTEMPTS):
                try:
                    os.replace(tmp, path)
                    return
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


def providers():
    out = ['mock']
    if os.environ.get('OPENAI_API_KEY') and os.environ.get('OPENAI_MODEL'): out.append('openai')
    if os.environ.get('ANTHROPIC_API_KEY'): out.append('anthropic')
    if os.environ.get('GEMINI_API_KEY') and os.environ.get('GEMINI_MODEL'): out.append('gemini')
    return out


def post_json(url, body, headers):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={'Content-Type': 'application/json', **headers})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read().decode())


# --- API adapters. NOT verified end-to-end in this repository (no keys were available during development). ---
def call_openai(prompt):
    j = post_json('https://api.openai.com/v1/chat/completions',
                  {'model': os.environ['OPENAI_MODEL'], 'messages': [{'role': 'user', 'content': prompt}]},
                  {'Authorization': 'Bearer ' + os.environ['OPENAI_API_KEY']})
    return j['choices'][0]['message']['content']


def call_anthropic(prompt):
    j = post_json('https://api.anthropic.com/v1/messages',
                  {'model': os.environ.get('ANTHROPIC_MODEL', 'claude-opus-5-5'), 'max_tokens': 4096,
                   'messages': [{'role': 'user', 'content': prompt}]},
                  {'x-api-key': os.environ['ANTHROPIC_API_KEY'], 'anthropic-version': '2023-06-01'})
    return ''.join(b.get('text', '') for b in j.get('content', []))


def call_gemini(prompt):
    model = urllib.parse.quote(os.environ['GEMINI_MODEL'])
    j = post_json(f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent',
                  {'contents': [{'parts': [{'text': prompt}]}]}, {'x-goog-api-key': os.environ['GEMINI_API_KEY']})
    return ''.join(p.get('text', '') for p in j['candidates'][0]['content']['parts'])


def mock_reply(case, task, to, packet):
    """Clearly-labelled fake answer, only for testing the transport path."""
    if task.endswith('-SYN'):
        return (f'[STUDIO_SYNTHESIS]\nCASE: {case}\nTASK: {task}\nFROM: {to}\nCOMMON: MOCK（模擬応答。内容は無意味）\n'
                f'DIFFERENCES: - MOCK | ORIGIN: OBSERVER\nUNKNOWN: MOCK\nHOLD: MOCK\nEXECUTION_NEEDS: MOCK\n'
                f'NEXT_TASK: MOCK（模擬応答）\nGATE_QUESTION: MOCK\n[/STUDIO_SYNTHESIS]')
    if task.endswith('-NEXT'):
        return f'[STUDIO_DONE]\nCASE: {case}\nTASK: {task}\nFROM: {to}\nCHANGED: なし（MOCK）\nVERIFIED: なし（MOCK）\nUNKNOWN: MOCK\nNEXT: MOCK\n[/STUDIO_DONE]'
    sources = re.findall(r'^- (S\d+)｜', packet, flags=re.M)
    acc = ', '.join(f'{s}=UNKNOWN（MOCKは資料を読まない）' for s in sources) or 'N/A'
    return (f'[STUDIO_RESULT]\nCASE: {case}\nTASK: {task}\nFROM: {to}\nSOURCE_ACCESS: {acc}\n'
            f'CONDITION: MOCK（模擬応答。AIではない）\nOBSERVATION: MOCK（内容は無意味）\nEVIDENCE: なし\nUNKNOWN: すべて\n[/STUDIO_RESULT]')


class Handler(BaseHTTPRequestHandler):
    server_version = 'AITeamStudioRelay/001'

    def cors(self):
        origin = self.headers.get('Origin', '')
        if ALLOWED_ORIGINS.match(origin):
            self.send_header('Access-Control-Allow-Origin', origin)
            self.send_header('Vary', 'Origin')
            self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
            self.send_header('Access-Control-Allow-Headers', 'Content-Type')
            self.send_header('Access-Control-Allow-Private-Network', 'true')

    def reply(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code); self.cors()
        self.send_header('Content-Type', 'application/json; charset=utf-8'); self.send_header('Content-Length', str(len(body)))
        self.end_headers(); self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204); self.cors(); self.end_headers()

    def do_GET(self):
        u = urllib.parse.urlparse(self.path); q = dict(urllib.parse.parse_qsl(u.query))
        try:
            if u.path == '/health':
                return self.reply(200, {'ok': True, 'providers': providers(), 'exchange': str(EXCHANGE)})
            if u.path == '/studio-config':
                # 2-E: default Studio settings (no secrets), so a browser whose storage was cleared can start configured.
                p = HERE / 'studio.config.json'
                if not p.is_file():
                    return self.reply(404, {'error': 'no studio.config.json next to the relay'})
                return self.reply(200, json.loads(p.read_text(encoding='utf-8')))
            if u.path in ('/snapshots', '/snapshot') and not self.local_origin():
                return self.reply(403, {'error': 'snapshots are for local pages only'})
            if u.path == '/snapshots':
                files = snapshot_files()
                return self.reply(200, {'dir': str(snapshots_dir()), 'snapshots': [
                    {'name': p.name, 'bytes': p.stat().st_size} for p in files[:50]], 'total': len(files),
                    'totalBytes': sum(p.stat().st_size for p in files)})
            if u.path == '/snapshot':
                name = q.get('name', '')
                hit = [p for p in snapshot_files() if p.name == name] if SNAPSHOT_NAME.match(name) else []
                if not hit:
                    return self.reply(404, {'error': 'no such snapshot'})
                return self.reply(200, {'name': name, 'text': hit[0].read_bytes().decode('utf-8')})
            if u.path == '/case-exists':
                # 2-B: lets the Studio avoid a Case ID whose folder is already here (read-only; nothing is created).
                return self.reply(200, {'exists': (EXCHANGE / safe(q['case'])).exists()})
            if u.path == '/result':
                d, to = EXCHANGE / safe(q['case']) / safe(q['task']), safe(q['to'])
                p, run = d / f'from-{to}.md', runner_state(d, to)
                recorded = run.pop('_replySha256', None) if run else None
                extra = {'runner': run} if run else {}
                if not p.exists():
                    return self.reply(200, {'pending': True, **extra})  # 200, not 404: polling is normal and should not log errors
                # A heuristic, not a completion guarantee: agents are asked to publish their reply atomically too
                # (StudioText.agentInstruction). Files changed within the last SETTLE_SECONDS (inclusive) wait.
                if time.time() - p.stat().st_mtime <= SETTLE_SECONDS:
                    return self.reply(200, {'pending': True, 'settling': True, **extra})  # possibly half-written by an agent
                # One read: the hash compared with the runner's record (R2) and the text come from the same bytes.
                data = p.read_bytes()
                if run and run['state'] == 'done':
                    run['check'] = runner_check(recorded, data)
                # Universal newlines, as read_text did: a reply written with CRLF reaches the Studio with LF (by design).
                text = data.decode('utf-8').replace('\r\n', '\n').replace('\r', '\n')
                return self.reply(200, {'text': text, **extra})
            if u.path == '/hash':
                url = q.get('url', '')
                if not re.match(r'^https?://', url):
                    return self.reply(400, {'error': 'http(s) URL only'})
                with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'AITeamStudioRelay'}), timeout=60) as r:
                    data = r.read(MAX_FETCH + 1)
                if len(data) > MAX_FETCH:
                    return self.reply(413, {'error': 'too large'})
                return self.reply(200, {'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data)})
            return self.reply(404, {'error': 'unknown path'})
        except KeyError as e:
            return self.reply(400, {'error': f'missing {e}'})
        except Exception as e:
            return self.reply(500, {'error': str(e)})

    def local_origin(self):
        """Snapshots hold the whole Studio record, so they are served only to pages on this machine (or to
        clients that send no Origin, such as tests), not to the GitHub Pages origin the CORS list allows."""
        origin = self.headers.get('Origin')
        return origin is None or bool(LOCAL_ORIGIN.match(origin))

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if path == '/snapshot':
            return self.post_snapshot()
        if path != '/task':
            return self.reply(404, {'error': 'unknown path'})
        try:
            n = int(self.headers.get('Content-Length', '0'))
            j = json.loads(self.rfile.read(min(n, 5_000_000)).decode())
            case, task, to, route, packet = j['case'], j['task'], j['to'], j.get('route', 'agent'), j['packet']
            d = EXCHANGE / safe(case) / safe(task); d.mkdir(parents=True, exist_ok=True)
            write_atomic(d / f'to-{safe(to)}.md', packet)  # byte-identical to the packet the Studio hashed
            if route == 'agent':
                to_file, from_file = d / f'to-{safe(to)}.md', d / f'from-{safe(to)}.md'
                return self.reply(200, {'queued': True, 'toFile': str(to_file), 'fromFile': str(from_file),
                                        'note': f'{to_file} に書き出しました。回答は同じフォルダの from-{safe(to)}.md'})
            if route == 'mock':
                text = mock_reply(case, task, to, packet)
            elif route.startswith('api:'):
                prov = route[4:]
                if prov not in providers():
                    return self.reply(400, {'error': f'{prov} のAPI設定がありません（proxy/.env を確認）'})
                text = {'openai': call_openai, 'anthropic': call_anthropic, 'gemini': call_gemini}[prov](packet)
            else:
                return self.reply(400, {'error': f'unknown route {route}'})
            write_atomic(d / f'from-{safe(to)}.md', text)
            return self.reply(200, {'text': text, 'note': route})
        except urllib.error.HTTPError as e:
            return self.reply(502, {'error': f'API HTTP {e.code}'})
        except Exception as e:
            return self.reply(500, {'error': str(e)})

    def post_snapshot(self):
        """2-C: store one copy of the Studio record as a new file (a generation). Nothing is overwritten or pruned:
        how long copies are kept is a human decision (README)."""
        if not self.local_origin():
            return self.reply(403, {'error': 'snapshots are for local pages only'})
        try:
            n = int(self.headers.get('Content-Length', '0'))
            if n > MAX_SNAPSHOT:
                return self.reply(413, {'error': f'too large ({n} bytes, limit {MAX_SNAPSHOT})'})
            j = json.loads(self.rfile.read(n).decode('utf-8'))
            text, rev = j['store'], int(j.get('rev', 0))
            problem = snapshot_problem(text)
            if problem:
                return self.reply(400, {'error': problem})
            if snapshots_dir().is_symlink():
                return self.reply(409, {'error': 'the snapshot folder is a symbolic link; copies are not written there'})
            snapshots_dir().mkdir(parents=True, exist_ok=True)
            with _SNAPSHOT_NAME_LOCK:
                stamp = time.strftime('%Y%m%dT%H%M%S', time.gmtime()) + f'{int(time.time() * 1000) % 1000:03d}Z'
                name, k = f'ats-store-{stamp}-r{rev}.json', 1
                while (snapshots_dir() / name).exists():
                    k += 1
                    name = f'ats-store-{stamp}-r{rev}-{k}.json'
                write_atomic(snapshots_dir() / name, text)
            data = text.encode('utf-8')
            files = snapshot_files()     # so the Studio can show how much is kept (retention is a human decision)
            return self.reply(200, {'saved': name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
                                    'count': len(files), 'totalBytes': sum(p.stat().st_size for p in files)})
        except (KeyError, ValueError, TypeError) as e:
            return self.reply(400, {'error': f'bad request: {e}'})
        except Exception as e:
            return self.reply(500, {'error': str(e)})

    def log_message(self, fmt, *args):
        sys.stderr.write('[relay] ' + (fmt % args) + '\n')


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--port', type=int, default=8787); a = ap.parse_args()
    load_env(); EXCHANGE.mkdir(exist_ok=True)
    print(f'AI Team Studio relay on http://127.0.0.1:{a.port}  providers={providers()}  exchange={EXCHANGE}')
    ThreadingHTTPServer(('127.0.0.1', a.port), Handler).serve_forever()


if __name__ == '__main__':
    main()
