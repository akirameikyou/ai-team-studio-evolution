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
import argparse, hashlib, json, os, re, sys, urllib.error, urllib.parse, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXCHANGE = HERE / 'exchange'
ALLOWED_ORIGINS = re.compile(r'^(https?://(127\.0\.0\.1|localhost)(:\d+)?|https://akirameikyou\.github\.io)$')
MAX_FETCH = 50 * 1024 * 1024


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
            if u.path == '/result':
                p = EXCHANGE / safe(q['case']) / safe(q['task']) / f"from-{safe(q['to'])}.md"
                if not p.exists():
                    return self.reply(200, {'pending': True})  # 200, not 404: polling is normal and should not log errors
                return self.reply(200, {'text': p.read_text(encoding='utf-8')})
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

    def do_POST(self):
        if urllib.parse.urlparse(self.path).path != '/task':
            return self.reply(404, {'error': 'unknown path'})
        try:
            n = int(self.headers.get('Content-Length', '0'))
            j = json.loads(self.rfile.read(min(n, 5_000_000)).decode())
            case, task, to, route, packet = j['case'], j['task'], j['to'], j.get('route', 'agent'), j['packet']
            d = EXCHANGE / safe(case) / safe(task); d.mkdir(parents=True, exist_ok=True)
            (d / f'to-{safe(to)}.md').write_text(packet, encoding='utf-8')
            if route == 'agent':
                return self.reply(200, {'queued': True, 'note': f'{d / ("to-" + safe(to) + ".md")} に書き出しました。回答は同じフォルダの from-{safe(to)}.md'})
            if route == 'mock':
                text = mock_reply(case, task, to, packet)
            elif route.startswith('api:'):
                prov = route[4:]
                if prov not in providers():
                    return self.reply(400, {'error': f'{prov} のAPI設定がありません（proxy/.env を確認）'})
                text = {'openai': call_openai, 'anthropic': call_anthropic, 'gemini': call_gemini}[prov](packet)
            else:
                return self.reply(400, {'error': f'unknown route {route}'})
            (d / f'from-{safe(to)}.md').write_text(text, encoding='utf-8')
            return self.reply(200, {'text': text, 'note': route})
        except urllib.error.HTTPError as e:
            return self.reply(502, {'error': f'API HTTP {e.code}'})
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
