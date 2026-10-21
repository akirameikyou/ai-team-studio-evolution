"""Stand-in for codex.exe in the tests. Behaviour comes from FAKE_MODE; every call is recorded in FAKE_RECORD."""
import json, os, sys, time

args = sys.argv[1:]
if args == ['--version']:
    print('fake-codex 0.0')
    sys.exit(0)
stdin = sys.stdin.buffer.read()
rec = os.environ.get('FAKE_RECORD')
if rec:
    with open(rec, 'a', encoding='utf-8') as f:
        f.write(json.dumps({'args': args, 'stdin': stdin.decode('utf-8')}, ensure_ascii=False) + '\n')
out = args[args.index('-o') + 1]
mode = os.environ.get('FAKE_MODE', 'ok')
if mode == 'fail':
    sys.exit(3)
if mode == 'slow':
    time.sleep(30)
if mode == 'person-first':      # a person publishes from-Codex.md while Codex is still working
    with open(os.path.join(os.path.dirname(out), 'from-Codex.md'), 'w', encoding='utf-8') as f:
        f.write('人が書いた返事')
reply = {'ok': os.environ.get('FAKE_REPLY', ''), 'empty': '  \n', 'person-first': 'runner reply'}.get(mode, '')
with open(out, 'wb') as f:
    f.write(reply.encode('utf-8'))
