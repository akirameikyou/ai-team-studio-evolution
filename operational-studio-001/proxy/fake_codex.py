"""Stand-in for codex.exe in the tests. The runner passes Codex only a small environment (R6), so the behaviour is read
from <-C folder>/fake.json ({mode, reply, occupy}) and every call is recorded in <-C folder>/calls.jsonl.
stderr imitates Codex: a header between two '--------' lines, then the prompt after a 'user' line (which the runner
must not log)."""
import json, os, sys, time

args = sys.argv[1:]
if args == ['--version']:
    print('fake-codex 0.0')
    sys.exit(0)
stdin = sys.stdin.buffer.read()
work = args[args.index('-C') + 1]
try:
    with open(os.path.join(work, 'fake.json'), encoding='utf-8') as f:
        cfg = json.load(f)
except FileNotFoundError:
    cfg = {}
with open(os.path.join(work, 'calls.jsonl'), 'a', encoding='utf-8') as f:
    f.write(json.dumps({'args': args, 'stdin': stdin.decode('utf-8'), 'env': sorted(os.environ)}, ensure_ascii=False) + '\n')
effort = next((a.split('=', 1)[1].strip('"') for a in args if a.startswith('model_reasoning_effort=')), 'none')
sys.stderr.buffer.write(('OpenAI Codex (fake)\n--------\nworkdir: x\nmodel: fake-model\nprovider: openai\napproval: never\n'
                         f'sandbox: read-only\nreasoning effort: {effort}\nsession id: 0000\n--------\nuser\n').encode('utf-8')
                        + stdin + b'\ncodex\nworking\n')
out = args[args.index('-o') + 1]
mode = cfg.get('mode', 'ok')
if mode == 'fail':
    sys.stderr.buffer.write(b'ERROR: fake failure\n')
    sys.exit(3)
if mode == 'slow':
    time.sleep(30)
if mode == 'person-first':      # a person publishes from-Codex.md while Codex is still working
    d = os.path.dirname(out)
    with open(os.path.join(d, 'from-Codex.md'), 'w', encoding='utf-8') as f:
        f.write('人が書いた返事')
    for name in cfg.get('occupy', []):                     # conflict names already taken
        with open(os.path.join(d, name), 'w', encoding='utf-8') as f:
            f.write('先にあった退避ファイル ' + name)
reply = {'ok': cfg.get('reply', ''), 'empty': '  \n', 'person-first': 'runner reply'}.get(mode, '')
with open(out, 'wb') as f:
    f.write(reply.encode('utf-8'))
