'use strict';
// Stage 1c state tests: request records (Q-1 store every request text, Q-2 append deliveries, Q-3 retry a failed delivery
// with the same Task ID and text). run1c(S) runs every test against a StudioState-like object.
function run1c(S) {

const results = []; let cur = null;
const check = (cond, msg) => results.push({ test: cur, ok: !!cond, msg });
const test = (name, fn) => { cur = name; try { fn(); } catch (e) { results.push({ test: name, ok: false, msg: 'ERROR ' + e.stack }); } };
let clock = Date.parse('2026-10-15T00:00:00Z');
const t = () => new Date(clock += 1000).toISOString();

const OBS = ['GPT', 'Claude Code'];
function newCase() {
  return { id: 'CASE-T', question: 'q', observers: OBS.slice(), sources: [], gateHistory: [], log: [], metrics: {},
    tasks: { GPT: { taskId: 'CASE-T-OBS-GPT', sentAt: t(), result: null }, 'Claude Code': { taskId: 'CASE-T-OBS-ClaudeCode', sentAt: t(), result: null } } };
}
const lines = o => Object.entries(o).map(([k, v]) => k + ': ' + v).join('\n');
const R = (from, task) => '[STUDIO_RESULT]\n' + lines({ CASE: 'CASE-T', TASK: task, FROM: from, SOURCE_ACCESS: 'S1=DIRECT', CONDITION: 'model X', OBSERVATION: '観測 ' + from, EVIDENCE: 'DIRECT 根拠', UNKNOWN: 'なし' }) + '\n[/STUDIO_RESULT]';
const SYN = task => '[STUDIO_SYNTHESIS]\n' + lines({ CASE: 'CASE-T', TASK: task, FROM: 'GPT', COMMON: '共通', DIFFERENCES: '差 | ORIGIN: SOURCE', UNKNOWN: '不明', HOLD: 'なし', EXECUTION_NEEDS: '必要', NEXT_TASK: '次', GATE_QUESTION: '問い' }) + '\n[/STUDIO_SYNTHESIS]';
const DONE = task => '[STUDIO_DONE]\n' + lines({ CASE: 'CASE-T', TASK: task, FROM: 'Claude Code', CHANGED: 'x', VERIFIED: 'y', UNKNOWN: 'なし', NEXT: 'z' }) + '\n[/STUDIO_DONE]';
const recv = (c, text, expectedTask, via = '貼り付け') => S.receive(c, text, { via, now: t(), rawSha256: null, expectedTask });
const rec = (c, kind, name, task, gen, packet) => S.recordRequest(c, { kind, name, task, gen, packet, sha256: 'sha-' + packet, at: t() });
const ok = (c, id, status, retry) => S.recordDelivery(c, id, { at: t(), status, connector: 'proxy', retry });
const fail = (c, id, error, retry) => S.recordDelivery(c, id, { at: t(), status: 'failed', connector: 'proxy', error, retry });
const state = c => JSON.stringify({ o: S.obsRevision(c), s: S.synRevision(c), g: c.generations || null, syn: c.synthesisReq || null, next: c.next || null, gate: c.gate || null });
function observed() { const c = newCase(); recv(c, R('GPT', 'CASE-T-OBS-GPT')); recv(c, R('Claude Code', 'CASE-T-OBS-ClaudeCode')); return c; }

test('Q-01 依頼文の記録と取り出し', () => {
  const c = newCase(), before = state(c);
  const r = rec(c, 'OBS', 'GPT', 'CASE-T-OBS-GPT', 1, 'P-obs-GPT');
  check(S.requests(c).length === 1 && r.packet === 'P-obs-GPT' && r.sha256 === 'sha-P-obs-GPT' && r.task === 'CASE-T-OBS-GPT' && r.gen === 1 && r.kind === 'OBS' && r.name === 'GPT', '本文・ハッシュ・Task ID・世代・種類・宛先を保存');
  check(S.currentRequest(c, 'OBS', 'GPT') === r, '今の依頼として取り出せる');
  check(S.currentRequest(c, 'OBS', 'Claude Code') === null && S.currentRequest(c, 'SYN') === null && S.currentRequest(c, 'NEXT') === null, '依頼していない宛先・種類は null');
  check(S.deliveryState(r) === 'unsent' && S.deliveryState(null) === null, '送付前は unsent、記録なしは null');
  ok(c, r.requestId, 'copied');
  check(S.deliveryState(r) === 'delivered', '手動コピー（copied）は送付済み（mutation C-02 で追加）');
  check(state(c) === before, '記録と送付結果の追記は、改訂番号・世代・統合・GATE・NEXT を変えない');
  const again = rec(c, 'OBS', 'GPT', 'CASE-T-OBS-GPT', 1, 'P-obs-GPT-again');
  check(S.currentRequest(c, 'OBS', 'GPT') === again && S.requests(c)[0] === r, '同じ Task ID に記録が2件あれば、新しい方が今の依頼。古い方も残る（mutation C-03 で追加）');
});

test('Q-02 送付結果の追記と送り直し可否', () => {
  const c = newCase(); const r = rec(c, 'OBS', 'GPT', 'CASE-T-OBS-GPT', 1, 'P');
  check(fail(c, r.requestId, '中継に接続できません').ok && S.deliveryState(r) === 'failed', '失敗を記録すると failed');
  check(S.canRetry(c, 'OBS', 'GPT'), '失敗した依頼は送り直せる');
  check(r.deliveries[0].error === '中継に接続できません' && r.deliveries[0].retry === false, '失敗の理由を残す');
  ok(c, r.requestId, 'queued', true);
  check(S.deliveryState(r) === 'delivered' && !S.canRetry(c, 'OBS', 'GPT'), '送り直しが成功すると delivered、送り直し不要');
  check(r.deliveries.length === 2 && r.deliveries[0].status === 'failed' && r.deliveries[1].retry === true, '送付結果は順に追記され、消えない');
  check(r.packet === 'P' && S.requests(c).length === 1, '送り直しで本文も記録の数も変わらない');
  check(!S.recordDelivery(c, 'no-such-id', { at: t(), status: 'copied' }).ok, '存在しない依頼への追記は拒否');
  const r2 = rec(c, 'OBS', 'Claude Code', 'CASE-T-OBS-ClaudeCode', 1, 'P2');
  check(!S.canRetry(c, 'OBS', 'Claude Code'), '送付前（unsent）は送り直しの対象ではない');
  ok(c, r2.requestId, 'shown');
  check(S.deliveryState(r2) === 'delivered', '画面に表示しての手渡し（shown）も送付済み');
});

test('Q-03 観測の再生成（新しい世代）と元の依頼の保存', () => {
  const c = newCase(); const r1 = rec(c, 'OBS', 'GPT', 'CASE-T-OBS-GPT', 1, 'P-gen1'); ok(c, r1.requestId, 'copied');
  const task2 = S.startObservationResend(c, 'GPT');
  check(task2 === 'CASE-T-OBS-GPT-2' && S.currentRequest(c, 'OBS', 'GPT') === null, '再生成すると今の依頼は新しい Task ID になり、まだ記録はない');
  const r2 = rec(c, 'OBS', 'GPT', task2, 2, 'P-gen2');
  check(S.currentRequest(c, 'OBS', 'GPT') === r2, '新しい世代の依頼が今の依頼');
  check(S.requests(c).length === 2 && S.requests(c)[0] === r1 && r1.packet === 'P-gen1' && r1.deliveries.length === 1, '元の世代の依頼文と送付結果はそのまま残る');
  fail(c, r2.requestId, 'x');
  check(S.canRetry(c, 'OBS', 'GPT'), '新しい世代の送付失敗は、その世代として送り直せる');
});

test('Q-04 統合依頼の送付失敗と送り直し', () => {
  const c = observed();
  const s = S.startSynthesisRequest(c, 'GPT', t()); check(s.ok && s.task === 'CASE-T-SYN', '統合を依頼');
  const r = rec(c, 'SYN', null, s.task, 1, 'P-syn');
  fail(c, r.requestId, '中継に接続できません');
  check(S.currentRequest(c, 'SYN') === r && S.canRetry(c, 'SYN'), '送付に失敗した統合依頼は送り直せる（行き詰まらない）');
  ok(c, r.requestId, 'queued', true);
  check(!S.canRetry(c, 'SYN') && S.deliveryState(r) === 'delivered', '送り直しが成功');
  const a = recv(c, SYN(s.task), s.task, 'ローカル中継');
  check(a.outcome === 'adopted' && c.synthesis && c.synthesis.task === s.task, '同じ Task ID への返事は、遅着ではなく採用される');
  const s2 = S.startSynthesisRequest(c, 'GPT', t());
  check(s2.task === 'CASE-T-SYN-2' && S.currentRequest(c, 'SYN') === null && S.requests(c)[0] === r, 'やり直すと新しい世代。前の依頼文は残る');
});

test('Q-05 実行タスクの送付失敗と送り直し', () => {
  const c = observed(); const s = S.startSynthesisRequest(c, 'GPT', t()); recv(c, SYN(s.task));
  S.recordGate(c, 'APPROVE', 'ok', t()); const n = S.startNext(c, 'Claude Code', 'P-next', t());
  check(n.ok && S.currentRequest(c, 'NEXT') === null, 'NEXT を作っただけでは依頼記録はない');
  const r = rec(c, 'NEXT', 'Claude Code', n.task, 1, 'P-next');
  fail(c, r.requestId, 'HTTP 500');
  check(S.canRetry(c, 'NEXT') && !S.canCountDone(c), '送付に失敗した実行タスクは送り直せる。完了には数えない');
  ok(c, r.requestId, 'queued', true);
  const d = recv(c, DONE(n.task), n.task, 'ローカル中継');
  check(d.outcome === 'adopted' && S.canCountDone(c) && !S.canRetry(c, 'NEXT'), '送り直した実行タスクへの完了報告は採用され、完了に数える');
});

test('Q-06 旧形式の Case（依頼記録なし）を書き換えない', () => {
  const legacy = { id: 'CASE-20261007-001', question: 'q', createdAt: '2026-10-07T08:59:35Z', sources: [], observers: ['GPT'], gateHistory: [], log: [], metrics: {},
    tasks: { GPT: { taskId: 'CASE-20261007-001-OBS-GPT', sentAt: '2026-10-07T08:59:44Z', sendStatus: null, result: { raw: 'r1', fields: { OBSERVATION: 'a' }, missing: [], format: '完全', at: '2026-10-07T09:00:00Z', via: '貼り付け' } } },
    synthesisReq: { to: 'GPT', at: '2026-10-07T09:02:23Z', status: 'copied', connector: 'manual' },
    next: { to: 'GPT', packet: 'old packet', at: '2026-10-07T09:03:15Z', status: 'copied', connector: 'manual' } };
  const before = JSON.stringify(legacy);
  const got = [S.currentRequest(legacy, 'OBS', 'GPT'), S.currentRequest(legacy, 'SYN'), S.currentRequest(legacy, 'NEXT')];
  check(got.every(x => x === null), '旧形式では今の依頼記録は null（作り直さない）');
  check(S.requests(legacy).length === 0 && !S.canRetry(legacy, 'OBS', 'GPT') && !S.canRetry(legacy, 'SYN') && !S.canRetry(legacy, 'NEXT'), '送り直しの対象もない');
  check(JSON.stringify(legacy) === before && !('requests' in legacy), '読むだけでは書き換えない（requests を足さない）');
});

test('Q-07 JSON の書き出しと読み込み', () => {
  const c = observed(); const s = S.startSynthesisRequest(c, 'GPT', t());
  const r1 = rec(c, 'OBS', 'GPT', 'CASE-T-OBS-GPT', 1, 'P1'); ok(c, r1.requestId, 'copied');
  const r2 = rec(c, 'SYN', null, s.task, 1, 'P2'); fail(c, r2.requestId, 'down');
  const snap = x => JSON.stringify({ q: S.requests(x).map(r => [r.requestId, r.task, r.packet, r.sha256, S.deliveryState(r)]), cur: [S.currentRequest(x, 'OBS', 'GPT') && S.currentRequest(x, 'OBS', 'GPT').requestId, S.currentRequest(x, 'SYN') && S.currentRequest(x, 'SYN').requestId], retry: [S.canRetry(x, 'OBS', 'GPT'), S.canRetry(x, 'SYN')] });
  const back = JSON.parse(JSON.stringify(c));
  check(snap(c) === snap(back), '依頼記録・送付状態・送り直し可否が一致 ' + snap(back));
});

test('Q-08 送付の記録に経路と中継が書いたファイルの場所を残す（段階1d-B、Codex C51 で追加）', () => {
  const c = newCase(); const r = rec(c, 'OBS', 'GPT', 'CASE-T-OBS-GPT', 1, 'P');
  S.recordDelivery(c, r.requestId, { at: t(), status: 'queued', connector: 'proxy', route: 'agent', toFile: 'C:\\ex\\CASE-T\\CASE-T-OBS-GPT\\to-GPT.md', fromFile: 'C:\\ex\\CASE-T\\CASE-T-OBS-GPT\\from-GPT.md' });
  const d = r.deliveries[0];
  check(d.route === 'agent' && d.toFile === 'C:\\ex\\CASE-T\\CASE-T-OBS-GPT\\to-GPT.md' && d.fromFile.endsWith('from-GPT.md'), '経路・依頼ファイル・結果ファイルを残す');
  check(S.deliveryState(r) === 'delivered', '送付の状態の判定は変わらない');
  S.recordDelivery(c, r.requestId, { at: t(), status: 'copied', connector: 'manual' });
  const m = r.deliveries[1];
  check(m.route === null && m.toFile === null && m.fromFile === null, '手動の送付では経路・場所は null');
  const back = JSON.parse(JSON.stringify(c));
  check(JSON.stringify(S.currentRequest(back, 'OBS', 'GPT').deliveries) === JSON.stringify(r.deliveries), 'JSON の書き出しと読み込みで変わらない');
});

return results;
}
if (typeof module !== 'undefined') module.exports = { run1c };
