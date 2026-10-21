'use strict';
// Stage 1b state tests (acceptance A-01..A-12 at the data/rule level). run1b(S) runs every test against a StudioState-like object.
function run1b(S) {

const results = []; let cur = null;
const check = (cond, msg) => results.push({ test: cur, ok: !!cond, msg });
const test = (name, fn) => { cur = name; try { fn(); } catch (e) { results.push({ test: name, ok: false, msg: 'ERROR ' + e.stack }); } };
let clock = Date.parse('2026-10-13T00:00:00Z');
const t = () => new Date(clock += 1000).toISOString();

const OBS = ['GPT', 'Claude Code'];
function newCase() {
  return { id: 'CASE-T', question: 'q', observers: OBS.slice(), sources: [], gateHistory: [], log: [], metrics: {},
    tasks: { GPT: { taskId: 'CASE-T-OBS-GPT', sentAt: t(), result: null }, 'Claude Code': { taskId: 'CASE-T-OBS-ClaudeCode', sentAt: t(), result: null } } };
}
const lines = o => Object.entries(o).map(([k, v]) => k + ': ' + v).join('\n');
const R = (from, task, extra = {}, close = true) => '[STUDIO_RESULT]\n' + lines({ CASE: 'CASE-T', TASK: task, FROM: from, SOURCE_ACCESS: 'S1=DIRECT', CONDITION: 'model X', OBSERVATION: '観測 ' + from, EVIDENCE: 'DIRECT 根拠', UNKNOWN: 'なし', ...extra }) + (close ? '\n[/STUDIO_RESULT]' : '');
const SYN = (task, extra = {}) => '[STUDIO_SYNTHESIS]\n' + lines({ CASE: 'CASE-T', TASK: task, FROM: 'GPT', COMMON: '共通', DIFFERENCES: '差 | ORIGIN: SOURCE', UNKNOWN: '不明', HOLD: 'なし', EXECUTION_NEEDS: '必要', NEXT_TASK: '次', GATE_QUESTION: '問い', ...extra }) + '\n[/STUDIO_SYNTHESIS]';
const DONE = (task, extra = {}) => '[STUDIO_DONE]\n' + lines({ CASE: 'CASE-T', TASK: task, FROM: 'Claude Code', CHANGED: 'x', VERIFIED: 'y', UNKNOWN: 'なし', NEXT: 'z', ...extra }) + '\n[/STUDIO_DONE]';
const BROKEN = '[STUDIO_RESULT]\nCASE: CASE-T\nOBSERVATION: 途中\n[/STUDIO_RESULT]';           // 2 keys -> PARSE_FAILED
const recv = (c, text, expectedTask, via = '貼り付け') => S.receive(c, text, { via, now: t(), rawSha256: null, expectedTask });
const attemptOf = (c, name) => c.tasks[name].result && c.tasks[name].result.attemptId;
const confirmAll = (c, kind, name) => { const rec = kind === 'RESULT' ? c.tasks[name].result : kind === 'SYN' ? c.synthesis : c.done; for (const it of S.pendingConfirmations(c, kind, rec, name)) S.setConfirmation(c, S.resultAttemptId(c, kind, rec, name), it, 'confirm', t()); };
// Full happy path up to an approved NEXT, used by several tests.
function approvedCase() {
  const c = newCase();
  recv(c, R('GPT', 'CASE-T-OBS-GPT')); recv(c, R('Claude Code', 'CASE-T-OBS-ClaudeCode'));
  S.startSynthesisRequest(c, 'GPT', t()); recv(c, SYN(c.synthesisReq.task));
  S.recordGate(c, 'APPROVE', 'ok', t()); S.startNext(c, 'Claude Code', 'packet', t());
  return c;
}

test('A-01 失敗→成功（確認済み）→失敗', () => {
  const c = newCase(), task = 'CASE-T-OBS-GPT';
  const a1 = recv(c, BROKEN, task, 'ローカル中継');
  check(a1.outcome === 'failed' && a1.assignment && a1.assignment.name === 'GPT', '1件目は失敗として GPT に記録');
  check(!c.tasks.GPT.result, '1件目では採用なし');
  const a2 = recv(c, R('GPT', task, { OBSERVATION: '観測したこと・答え' }), task, 'ローカル中継');    // TEMPLATE_VALUE -> needs confirmation
  check(a2.outcome === 'adopted', '2件目を採用');
  check(!S.eligibleRecord(c, 'RESULT', c.tasks.GPT.result, 'GPT'), '確認前は利用不可');
  confirmAll(c, 'RESULT', 'GPT');
  check(S.eligibleRecord(c, 'RESULT', c.tasks.GPT.result, 'GPT'), '確認後は利用可能');
  recv(c, R('Claude Code', 'CASE-T-OBS-ClaudeCode'));
  S.startSynthesisRequest(c, 'GPT', t()); recv(c, SYN(c.synthesisReq.task)); S.recordGate(c, 'APPROVE', '', t()); S.startNext(c, 'Claude Code', 'p', t());
  const before = { rev: S.obsRevision(c), adopted: attemptOf(c, 'GPT'), conf: S.confirmedItems(c, a2.attemptId).join(), canNext: S.canSendNext(c) };
  const a3 = recv(c, BROKEN, task, 'ローカル中継');
  check(a3.outcome === 'failed', '3件目は失敗');
  check(S.attempts(c).filter(a => a.assignment && a.assignment.name === 'GPT').length === 3 && S.receivedCount(c, 'GPT') === 3, '試行3件・受信数3');
  check(attemptOf(c, 'GPT') === a2.attemptId && before.adopted === a2.attemptId, '採用中は2件目のまま');
  check(S.obsRevision(c) === before.rev, 'obsRevision は変わらない');
  check(S.confirmedItems(c, a2.attemptId).join() === before.conf, '確認状態は変わらない');
  check(!S.synthesisStale(c) && !S.gateStale(c) && !S.nextStale(c) && S.canSendNext(c) === before.canNext, '統合・GATE・NEXT は変わらない');
  check(S.attempts(c)[0].raw === BROKEN && S.attempts(c)[0].via === 'ローカル中継' && S.attempts(c)[0].parsed.RESULT.status === 'PARSE_FAILED', '1件目の原文・経路・タグ別結果が残る');
});

test('A-02 受信数と利用可能数', () => {
  const c = newCase(); c.observers.push('Gemini', 'Codex', 'Meta');
  for (const n of ['Gemini', 'Codex', 'Meta']) c.tasks[n] = { taskId: 'CASE-T-OBS-' + n, sentAt: t(), result: null };
  check(!S.canRequestSynthesis(c), '利用可能0件では統合を依頼できない');
  check(!S.startSynthesisRequest(c, 'GPT', t()).ok, '統合依頼の処理も拒否');
  recv(c, R('GPT', 'CASE-T-OBS-GPT', {}, true).replace('EVIDENCE: DIRECT 根拠\n', ''));        // PARTIAL
  recv(c, R('Claude Code', 'CASE-T-OBS-ClaudeCode', {}, false));                                 // UNCONFIRMED (UNCLOSED)
  recv(c, 'ふつうの文章の返事です', 'CASE-T-OBS-Gemini');                                          // NO_BLOCK via expected task
  c.tasks.Codex.result = { raw: 'old', fields: { OBSERVATION: 'old' }, missing: [], format: '完全', at: '2026-10-07T00:00:00Z', via: '貼り付け' };   // old format
  check(c.tasks.GPT.result.status === 'PARTIAL' && S.eligibleRecord(c, 'RESULT', c.tasks.GPT.result, 'GPT'), 'PARTIAL は利用可能');
  check(c.tasks['Claude Code'].result.status === 'UNCONFIRMED' && !S.eligibleRecord(c, 'RESULT', c.tasks['Claude Code'].result, 'Claude Code'), 'UNCLOSED 未確認は利用不可');
  check(c.tasks.Gemini.result.status === 'NO_BLOCK' && S.eligibleRecord(c, 'RESULT', c.tasks.Gemini.result, 'Gemini'), 'NO_BLOCK は段階1で利用可能');
  check(S.eligibleRecord(c, 'RESULT', c.tasks.Codex.result, 'Codex'), '旧形式は利用可能');
  check(S.eligibleObservers(c).sort().join() === ['Codex', 'GPT', 'Gemini'].join(), '利用可能数3（GPT・Gemini・Codex）');
  check(S.receivedCount(c, 'Claude Code') === 1 && S.receivedCount(c, 'Codex') === 1 && S.receivedCount(c, 'Meta') === 0, '受信数は利用可能数と別');
  confirmAll(c, 'RESULT', 'Claude Code');
  check(S.eligibleRecord(c, 'RESULT', c.tasks['Claude Code'].result, 'Claude Code'), 'UNCLOSED 確認後は利用可能');
});

test('A-03 確認必須の警告（3種）', () => {
  const cases = [['値の違う重複', R('GPT', 'CASE-T-OBS-GPT') .replace('[/STUDIO_RESULT]', 'OBSERVATION: 別の値\n[/STUDIO_RESULT]')],
                 ['分類未確定', R('GPT', 'CASE-T-OBS-GPT').replace('UNKNOWN: なし', 'メモ: x\nUNKNOWN: なし')],
                 ['見本の文言', R('GPT', 'CASE-T-OBS-GPT', { CONDITION: 'モデル / 使えた道具 / ファイル可否 / Web可否 / 事前知識の有無' })]];
  for (const [label, text] of cases) {
    const c = newCase(); recv(c, text);
    const id = attemptOf(c, 'GPT'), pend = S.pendingConfirmations(c, 'RESULT', c.tasks.GPT.result, 'GPT');
    check(pend.length === 1, label + ': 確認項目1つ ' + pend.join());
    check(!S.eligibleObservers(c).includes('GPT'), label + ': 未確認は利用可能に入らない（差分・統合依頼・GATE の入力外）');
    S.setConfirmation(c, id, pend[0], 'confirm', t());
    check(S.eligibleObservers(c).includes('GPT'), label + ': 確認後は入る');
    S.setConfirmation(c, id, pend[0], 'revoke', t());
    check(!S.eligibleObservers(c).includes('GPT'), label + ': 取り消すと再び外れる');
  }
});

test('A-04 表示だけの警告', () => {
  const c = newCase();
  recv(c, R('GPT', 'CASE-T-OBS-GPT', { UNKNOWN: '（なし）' }));
  check(c.tasks.GPT.result.warnings.includes('PARENTHESIZED_VALUE:UNKNOWN') && S.eligibleRecord(c, 'RESULT', c.tasks.GPT.result, 'GPT'), '括弧の値は警告だけで利用可能');
  recv(c, R('Claude Code', 'CASE-T-OBS-ClaudeCode').replace('[/STUDIO_RESULT]', 'OBSERVATION: 観測 Claude Code\n[/STUDIO_RESULT]'));
  const r = c.tasks['Claude Code'].result;
  check(r.warnings.includes('DUPLICATE_KEY:OBSERVATION') && r.conflicts.length === 0 && S.eligibleRecord(c, 'RESULT', r, 'Claude Code'), '同じ値の重複は conflicts なしで利用可能');
});

test('A-05 形式外（NO_BLOCK）', () => {
  const c = newCase();
  const a = recv(c, 'ただの文章');
  check(a.outcome === 'noblock' && !a.assignment, '宛先不明として記録');
  S.assign(c, a.attemptId, { kind: 'RESULT', name: 'GPT' }, t());
  const r = c.tasks.GPT.result;
  check(r.status === 'NO_BLOCK' && r.reason === null && Object.keys(r.fields).length === 0 && r.missing.length === 8 && r.conflicts.length === 0 && r.selected === null && r.candidates.length === 0 && r.ruleVersion === 'v2', '保存形が契約どおり');
  recv(c, R('Claude Code', 'CASE-T-OBS-ClaudeCode'));
  const before = attemptOf(c, 'Claude Code');
  const b = recv(c, 'また文章', 'CASE-T-OBS-ClaudeCode');
  check(b.outcome === 'kept' && attemptOf(c, 'Claude Code') === before, '構造化された結果は NO_BLOCK で置き換わらない');
  const c2 = newCase(); c2.tasks.GPT.result = { raw: 'old', fields: { OBSERVATION: 'old' }, missing: [], format: '完全', at: '2026-10-07T00:00:00Z' };
  const b2 = recv(c2, '文章', 'CASE-T-OBS-GPT');
  check(b2.outcome === 'kept' && c2.tasks.GPT.result.raw === 'old', '旧形式の結果も置き換わらない');
  const c3 = approvedCase(); const n = recv(c3, '統合のつもりの文章');
  const r3 = S.assign(c3, n.attemptId, { kind: 'SYN' }, t());
  check(r3.ok && r3.outcome !== 'adopted' && c3.synthesis.raw !== '統合のつもりの文章', '統合への形式外は採用されない');
});

test('A-06 複数種のタグ（D12=A）', () => {
  let c = newCase(); recv(c, R('GPT', 'CASE-T-OBS-GPT')); S.startSynthesisRequest(c, 'GPT', t());
  let a = recv(c, SYN(c.synthesisReq.task) + '\n' + R('Claude Code', 'CASE-T-OBS-ClaudeCode'));
  check(a.picked === 'SYN' && a.outcome === 'adopted' && a.notAdopted.length === 1 && a.notAdopted[0].tag === 'RESULT' && a.notAdopted[0].status === 'OK' && /優先順/.test(a.notAdopted[0].why), '有効2種: 統合を採用、観測は notAdopted');
  check(!c.tasks['Claude Code'].result, '観測は採用されない');
  c = newCase(); S.startSynthesisRequest(c, 'GPT', t());
  a = recv(c, '[STUDIO_SYNTHESIS]\nCASE: x\n[/STUDIO_SYNTHESIS]\n' + R('GPT', 'CASE-T-OBS-GPT'));
  check(a.picked === 'RESULT' && a.outcome === 'adopted' && a.parsed.SYN.status === 'PARSE_FAILED', '失敗＋有効: 有効な観測を採用');
  c = newCase(); a = recv(c, BROKEN + '\n[STUDIO_DONE]\nCASE: x\n[/STUDIO_DONE]');
  check(a.outcome === 'failed' && !a.picked, '失敗のみ複数: 失敗として記録');
  c = approvedCase(); a = recv(c, SYN(c.synthesisReq.task) + DONE(c.next.task) + R('GPT', 'CASE-T-OBS-GPT'));
  check(a.picked === 'SYN' && a.notAdopted.map(x => x.tag).join() === 'DONE,RESULT', '有効3種: 統合を採用、他2種は notAdopted');
});

test('A-07 失効（a 切り替え・b 取り消し・c 追加・d 取り消し→再確認）', () => {
  const blocked = (c, tag) => {
    check(S.synthesisStale(c) && S.gateStale(c) && S.nextStale(c), tag + ': 統合・GATE・NEXT が古い');
    check(!S.canGate(c) && !S.canSendNext(c), tag + ': GATE・NEXT 送信ができない');
    check(!S.recordGate(c, 'APPROVE', '', t()).ok && !S.startNext(c, 'Claude Code', 'p', t()).ok, tag + ': 処理の中でも拒否');
    check(c.gateHistory.length >= 1 && c.next && c.synthesis, tag + ': 記録は残る');
  };
  let c = approvedCase();
  recv(c, R('GPT', 'CASE-T-OBS-GPT', { OBSERVATION: '新しい観測' })); blocked(c, '(a)');
  const r = S.startSynthesisRequest(c, 'GPT', t()); check(r.ok && r.task === 'CASE-T-SYN-2', '(a) 統合をやり直すと世代2');
  check(c.archive.length === 1 && c.archive[0].gate && c.archive[0].next, '(a) 古い統合・GATE・NEXT は archive に残る');
  check(recv(c, DONE('CASE-T-NEXT')).outcome === 'late' || !c.done, '(a) 再統合中の古い DONE は採用されない');
  recv(c, SYN('CASE-T-SYN-2')); check(S.recordGate(c, 'APPROVE', '', t()).ok && S.startNext(c, 'Claude Code', 'p', t()).ok && c.next.task === 'CASE-T-NEXT-2', '(a) 新しい GATE・NEXT（世代2）に進める');

  c = newCase(); recv(c, R('GPT', 'CASE-T-OBS-GPT', { CONDITION: 'モデル / 使えた道具 / ファイル可否 / Web可否 / 事前知識の有無' })); confirmAll(c, 'RESULT', 'GPT');
  S.startSynthesisRequest(c, 'GPT', t()); recv(c, SYN(c.synthesisReq.task)); S.recordGate(c, 'APPROVE', '', t()); S.startNext(c, 'Claude Code', 'p', t());
  const id = attemptOf(c, 'GPT'), item = 'TEMPLATE_VALUE:CONDITION';
  S.setConfirmation(c, id, item, 'revoke', t()); blocked(c, '(b)');
  S.setConfirmation(c, id, item, 'confirm', t());
  check(S.synthesisStale(c) && S.gateStale(c) && !S.canSendNext(c), '(d) 再確認しても古い GATE は復活しない');

  c = newCase(); recv(c, R('GPT', 'CASE-T-OBS-GPT'));
  S.startSynthesisRequest(c, 'GPT', t()); recv(c, SYN(c.synthesisReq.task)); S.recordGate(c, 'APPROVE', '', t()); S.startNext(c, 'Claude Code', 'p', t());
  recv(c, R('Claude Code', 'CASE-T-OBS-ClaudeCode')); blocked(c, '(c)');

  // Same rule on the synthesis side: a synthesis confirmation revoked and confirmed again must not revive the old GATE.
  c = newCase(); recv(c, R('GPT', 'CASE-T-OBS-GPT')); S.startSynthesisRequest(c, 'GPT', t());
  recv(c, SYN(c.synthesisReq.task, { HOLD: '判断を保留すべきこと' }));
  check(!S.canGate(c), '(e) 統合に確認必須の警告がある間は GATE できない');
  const sid = c.synthesis.attemptId, sitem = 'TEMPLATE_VALUE:HOLD';
  S.setConfirmation(c, sid, sitem, 'confirm', t()); S.recordGate(c, 'APPROVE', '', t()); S.startNext(c, 'Claude Code', 'p', t());
  check(S.canSendNext(c) && !S.gateStale(c), '(e) 確認後に GATE・NEXT');
  S.setConfirmation(c, sid, sitem, 'revoke', t()); S.setConfirmation(c, sid, sitem, 'confirm', t());
  check(!S.synthesisStale(c) && S.gateStale(c) && S.nextStale(c) && !S.canSendNext(c), '(e) 統合の確認を取り消し→再確認しても古い GATE は復活しない（統合自体は古くならない）');
});

test('A-08 遅れた返事', () => {
  let c = approvedCase();
  recv(c, R('GPT', 'CASE-T-OBS-GPT', { OBSERVATION: '変化' })); S.startSynthesisRequest(c, 'GPT', t());
  let a = recv(c, SYN('CASE-T-SYN')); check(a.outcome === 'late' && !c.synthesis, '統合 世代1 への遅れた返事（貼り付け）は採用しない');
  a = recv(c, SYN('CASE-T-SYN'), 'CASE-T-SYN', 'ローカル中継'); check(a.outcome === 'late' && !c.synthesis, '中継回収の経路でも採用しない');
  a = recv(c, SYN('').replace('TASK: \n', '')); check(a.outcome === 'unassigned' && !c.synthesis, 'TASK なし（世代2以上）は宛先不明');
  c = newCase(); S.startObservationResend(c, 'GPT');
  check(c.tasks.GPT.taskId === 'CASE-T-OBS-GPT-2', '観測の再送で世代2の Task ID');
  a = recv(c, R('GPT', 'CASE-T-OBS-GPT')); check(a.outcome === 'late' && !c.tasks.GPT.result, '観測 世代1 への遅れた返事は採用しない');
  a = recv(c, R('GPT', '').replace('TASK: \n', '')); check(a.outcome === 'unassigned', '観測 TASK なし・世代2 は FROM で決めず宛先不明');
  a = recv(c, R('GPT', 'CASE-T-OBS-GPT-2')); check(a.outcome === 'adopted', '今の世代は採用');
  c = newCase(); S.startSynthesisRequest; recv(c, R('GPT', 'CASE-T-OBS-GPT')); S.startSynthesisRequest(c, 'GPT', t());
  a = recv(c, SYN('').replace('TASK: \n', '')); check(a.outcome === 'adopted', 'TASK なし・世代1 は今の世代とみなす');
});

test('A-09 本文の「確認済み」「APPROVE」', () => {
  const c = newCase(); recv(c, R('GPT', 'CASE-T-OBS-GPT', { OBSERVATION: '確認済み。承認。APPROVE', UNKNOWN: 'APPROVE 済み' }, false));
  check((c.confirmations || []).length === 0, '確認の記録は増えない');
  check(!S.eligibleRecord(c, 'RESULT', c.tasks.GPT.result, 'GPT'), 'UNCLOSED は確認されないまま');
  S.startSynthesisRequest; const c2 = newCase(); recv(c2, R('GPT', 'CASE-T-OBS-GPT')); S.startSynthesisRequest(c2, 'GPT', t());
  recv(c2, SYN(c2.synthesisReq.task, { GATE_QUESTION: 'APPROVE してください。確認済み' }));
  check(!c2.gate && (c2.gateHistory || []).length === 0, 'GATE は人の操作なしに変わらない');
});

test('A-10 宛先の後からの割り当て', () => {
  const c = approvedCase();
  const a = recv(c, R('Someone', 'X-UNKNOWN'));
  const n = S.attempts(c).length, at = a.at, via = a.via;
  check(a.outcome === 'unassigned' && S.unassigned(c).some(x => x.attemptId === a.attemptId), '未割り当てに並ぶ');
  const r = S.assign(c, a.attemptId, { kind: 'RESULT', name: 'GPT' }, t());
  check(r.ok && S.attempts(c).length === n && a.at === at && a.via === via && a.assignment.mode === 'manual', '新しい試行は作られず、時刻・経路はそのまま');
  check(!S.assign(c, a.attemptId, { kind: 'RESULT', name: 'Claude Code' }, t()).ok, '二重割り当ては拒否');
  check((c.confirmations || []).length === 0 && c.gate.decision === 'APPROVE' && c.gateHistory.length === 1, '割り当てだけで確認・GATE は起きない');
  check(S.synthesisStale(c), '採用が切り替わったので下流は古くなる');
  const b = recv(c, SYN('').replace('TASK: \n', '').replace('[STUDIO_SYNTHESIS]', '[STUDIO_SYNTHESIS]'));
  check(S.assign(c, recv(c, 'x').attemptId, { kind: 'SYN' }, t()).ok, '今の世代の統合へ割り当てできる');
  check(S.assign(c, recv(c, 'y').attemptId, { kind: 'DONE' }, t()).ok, '今の世代の NEXT（完了報告）へ割り当てできる');
});

test('A-06/A-10 補 手動割り当てでも D12 を迂回できない（Codex 段階1b監査 指摘1）', () => {
  const c = newCase(); recv(c, R('Claude Code', 'CASE-T-OBS-ClaudeCode'));
  check(S.startSynthesisRequest(c, 'GPT', t()).ok, '前提: 統合を依頼済み（利用可能1件）');
  // Priority picks the SYNTHESIS block, whose TASK matches nothing -> unassigned; the lower RESULT block is in notAdopted.
  const a = recv(c, SYN('X-NOT-A-TASK') + '\n' + R('GPT', 'CASE-T-OBS-GPT'));
  check(a.picked === 'SYN' && a.outcome === 'unassigned' && a.notAdopted.some(x => x.tag === 'RESULT'), '優先された統合が宛先不明、観測は notAdopted');
  const r1 = S.assign(c, a.attemptId, { kind: 'RESULT', name: 'GPT' }, t());
  check(!r1.ok && !c.tasks.GPT.result && !a.assignment, '観測への手動割り当ては拒否され、観測は採用されない');
  const r2 = S.assign(c, a.attemptId, { kind: 'SYN' }, t());
  check(r2.ok && r2.outcome === 'adopted' && c.synthesis && c.synthesis.attemptId === a.attemptId, '優先されたタグ（統合）への割り当ては宛先の補完として採用');
  const b = recv(c, BROKEN); check(b.outcome === 'failed' && !b.picked, '失敗の受信は picked なし');
  check(S.assign(c, b.attemptId, { kind: 'RESULT', name: 'GPT' }, t()).ok && !c.tasks.GPT.result, '失敗の受信は記録として割り当てでき、採用はされない');
});

test('A-06/A-10 補2 整合しない JSON 記録からも D12 を迂回できない（Codex 段階1b再監査 b）', () => {
  const c = newCase(); recv(c, R('Claude Code', 'CASE-T-OBS-ClaudeCode')); S.startSynthesisRequest(c, 'GPT', t());
  recv(c, SYN('X-NOT-A-TASK') + '\n' + R('GPT', 'CASE-T-OBS-GPT'));
  const back = JSON.parse(JSON.stringify(c));                       // export -> import
  const a = S.attempts(back)[S.attempts(back).length - 1]; a.picked = null;   // edited / inconsistent file
  for (const kind of ['RESULT', 'SYN']) {
    const r = S.assign(back, a.attemptId, kind === 'RESULT' ? { kind, name: 'GPT' } : { kind }, t());
    check(!r.ok && !a.assignment, kind + ' への割り当ては拒否（採用できるタグがあるのに picked なし）');
  }
  check(!back.tasks.GPT.result && !back.synthesis, '何も採用されない');
  const b = recv(back, 'ただの文章'); check(!b.picked && S.assign(back, b.attemptId, { kind: 'RESULT', name: 'GPT' }, t()).ok, '形式外（全タグ null）の割り当ては従来どおりできる');
});

test('A-07 補 古い NEXT への完了報告は採用しない（Codex 段階1b監査 指摘2）', () => {
  const c = approvedCase(); const nextTask = c.next.task;
  recv(c, R('GPT', 'CASE-T-OBS-GPT', { OBSERVATION: '変化' }));
  check(S.nextStale(c), 'NEXT は古い');
  let a = recv(c, DONE(nextTask)); check(a.outcome === 'stale' && !c.done, '貼り付けの完了報告は記録のみ');
  a = recv(c, DONE(nextTask), nextTask, 'ローカル中継'); check(a.outcome === 'stale' && !c.done, '中継・API の経路でも記録のみ');
  const c2 = approvedCase(); const ok = recv(c2, DONE(c2.next.task));
  check(ok.outcome === 'adopted' && S.canCountDone(c2), '有効な NEXT への完了報告は採用され、完了に数える');
});

test('A-11 JSON の書き出しと読み込み', () => {
  const c = approvedCase();
  recv(c, R('GPT', 'CASE-T-OBS-GPT', { CONDITION: 'モデル / 使えた道具 / ファイル可否 / Web可否 / 事前知識の有無' }));
  const id = attemptOf(c, 'GPT'); S.setConfirmation(c, id, 'TEMPLATE_VALUE:CONDITION', 'confirm', t()); S.setConfirmation(c, id, 'TEMPLATE_VALUE:CONDITION', 'revoke', t());
  recv(c, 'unassigned text');
  const snap = x => JSON.stringify({ e: S.eligibleObservers(x), r: [S.obsRevision(x), S.synRevision(x)], st: [S.synthesisStale(x), S.gateStale(x), S.nextStale(x), S.doneStale(x)],
    can: [S.canRequestSynthesis(x), S.canGate(x), S.canSendNext(x), S.canCountDone(x)], u: S.unassigned(x).length, rc: OBS.map(n => S.receivedCount(x, n)) });
  const back = JSON.parse(JSON.stringify(c));
  check(snap(c) === snap(back), '採用・利用可能・改訂番号・古い判定・操作の可否が一致 ' + snap(back));
  check(!S.canSendNext(back), '取り消し済みの古い GATE は読み込み後も有効にならない');
});

test('A-12 旧形式の Case（試験データ: 現行 app.js の保存形で手作り）', () => {
  const legacy = { id: 'CASE-20261007-001', question: 'q', createdAt: '2026-10-07T08:59:35Z', sources: [], observers: ['GPT', 'Claude Code'], gateHistory: [], log: [], metrics: {},
    tasks: { GPT: { taskId: 'CASE-20261007-001-OBS-GPT', sentAt: '2026-10-07T08:59:44Z', sendStatus: null, result: { raw: 'r1', fields: { OBSERVATION: 'a' }, missing: [], format: '完全', at: '2026-10-07T09:00:00Z', via: '貼り付け' } },
             'Claude Code': { taskId: 'CASE-20261007-001-OBS-ClaudeCode', sentAt: '2026-10-07T08:59:35Z', sendStatus: null, result: { raw: 'r2', fields: { OBSERVATION: 'b' }, missing: [], format: '完全', at: '2026-10-07T09:01:36Z', via: 'ローカル中継' } } },
    synthesisReq: { to: 'Claude Code', at: '2026-10-07T09:02:23Z', status: 'queued', connector: 'proxy' },
    synthesis: { by: 'Claude Code', raw: 's', fields: { COMMON: 'c' }, missing: [], at: '2026-10-07T09:02:48Z', via: 'ローカル中継' } };
  const gate = { decision: 'APPROVE', reason: 'ok', at: '2026-10-07T09:03:11Z', synthesisAt: '2026-10-07T09:02:48Z' };
  legacy.gate = gate; legacy.gateHistory = [gate];
  legacy.next = { to: 'Claude Code', packet: 'p', at: '2026-10-07T09:03:15Z', status: 'queued', connector: 'proxy' };
  legacy.done = { by: 'Claude Code', raw: 'd', fields: { CHANGED: 'x' }, at: '2026-10-07T09:03:31Z', via: 'ローカル中継' };
  const before = JSON.stringify(legacy);
  check(S.eligibleObservers(legacy).length === 2, '旧形式の観測は利用可能');
  check(!S.synthesisStale(legacy) && !S.gateStale(legacy) && !S.nextStale(legacy) && !S.doneStale(legacy), '採用済みの統合・GATE・NEXT・DONE を古いと誤表示しない');
  check(S.canGate(legacy) && S.canSendNext(legacy) && S.canCountDone(legacy), '今までどおり使える');
  check(S.synTaskId(legacy) === 'CASE-20261007-001-SYN' && S.nextTaskId(legacy) === 'CASE-20261007-001-NEXT', '世代1は旧 ID と同じ');
  check(S.receivedCount(legacy, 'GPT') === 1, '旧形式の結果は受信1件');
  check(JSON.stringify(legacy) === before, '判定・表示のための読み取りで保存データが書き換わらない（仮の ID は保存されない）');
});

test('世代と Task ID', () => {
  const c = newCase();
  check(S.obsTaskId(c, 'Claude Code') === 'CASE-T-OBS-ClaudeCode', '観測 世代1 は旧形式の ID');
  S.startObservationResend(c, 'Claude Code'); S.startObservationResend(c, 'Claude Code');
  check(c.tasks['Claude Code'].taskId === 'CASE-T-OBS-ClaudeCode-3', '再送2回で世代3');
});

// 3-1-r2 R2 (G1): a reply the runner's record does not vouch for is adopted as before, but needs a person's confirmation.
const recvRun = (c, text, task, meta) => S.receive(c, text, { via: 'ローカル中継', now: t(), rawSha256: null, expectedTask: task, ...meta });
test('RN-B1 実行係の記録と一致した返事は、今までどおり使える', () => {
  const c = newCase(), task = 'CASE-T-OBS-GPT';
  const a = recvRun(c, R('GPT', task), task, { runner: { condition: { route: 'runner' }, seconds: 3, check: 'match' } });
  check(a.outcome === 'adopted' && a.runner && a.runner.check === 'match' && !a.runnerCheck, '採用し、条件を記録（照合の印は付かない）');
  check(S.pendingConfirmations(c, 'RESULT', c.tasks.GPT.result, 'GPT').length === 0, '確認の項目は増えない');
  check(S.eligibleRecord(c, 'RESULT', c.tasks.GPT.result, 'GPT'), '利用可能');
});
test('RN-B2 一致しない・照合できない返事は、採用するが確認するまで使えない', () => {
  for (const check_ of ['mismatch', 'unverifiable']) {
    const c = newCase(), task = 'CASE-T-OBS-GPT';
    const a = recvRun(c, R('GPT', task), task, { runnerCheck: check_ });
    check(a.outcome === 'adopted' && a.runnerCheck === check_ && !a.runner, check_ + '：採用は今までどおり・条件は付かない・照合の結果を記録');
    const rec = c.tasks.GPT.result;
    check(JSON.stringify(S.pendingConfirmations(c, 'RESULT', rec, 'GPT')) === JSON.stringify([S.RUNNER_UNVERIFIED]), check_ + '：確認の項目 RUNNER_UNVERIFIED が必須');
    check(!S.eligibleRecord(c, 'RESULT', rec, 'GPT'), check_ + '：確認前は利用不可');
    check(S.eligibleObservers(c).length === 0, check_ + '：統合に使える観測に数えない');
    S.setConfirmation(c, S.resultAttemptId(c, 'RESULT', rec, 'GPT'), S.RUNNER_UNVERIFIED, 'confirm', t());
    check(S.eligibleRecord(c, 'RESULT', rec, 'GPT'), check_ + '：確認した後は利用可能');
    S.setConfirmation(c, S.resultAttemptId(c, 'RESULT', rec, 'GPT'), S.RUNNER_UNVERIFIED, 'revoke', t());
    check(!S.eligibleRecord(c, 'RESULT', rec, 'GPT'), check_ + '：取り消すと再び利用不可');
  }
});
test('RN-B3 返事の規則による確認と、実行係の確認は両方とも必要', () => {
  const c = newCase(), task = 'CASE-T-OBS-GPT';
  recvRun(c, R('GPT', task, { OBSERVATION: '観測したこと・答え' }), task, { runnerCheck: 'mismatch' });   // TEMPLATE_VALUE too
  const rec = c.tasks.GPT.result, id = S.resultAttemptId(c, 'RESULT', rec, 'GPT');
  const p = S.pendingConfirmations(c, 'RESULT', rec, 'GPT');
  check(p.includes(S.RUNNER_UNVERIFIED) && p.some(x => x.startsWith('TEMPLATE_VALUE:')), '2種類の確認がどちらも必要 ' + p.join(','));
  S.setConfirmation(c, id, S.RUNNER_UNVERIFIED, 'confirm', t());
  check(!S.eligibleRecord(c, 'RESULT', rec, 'GPT'), '実行係の確認だけでは使えない');
  for (const it of S.pendingConfirmations(c, 'RESULT', rec, 'GPT')) S.setConfirmation(c, id, it, 'confirm', t());
  check(S.eligibleRecord(c, 'RESULT', rec, 'GPT'), '両方確認すると使える');
});
test('RN-B4 統合の返事でも同じ', () => {
  const c = newCase();
  recv(c, R('GPT', 'CASE-T-OBS-GPT')); recv(c, R('Claude Code', 'CASE-T-OBS-ClaudeCode'));
  S.startSynthesisRequest(c, 'GPT', t());
  recvRun(c, SYN(c.synthesisReq.task), c.synthesisReq.task, { runnerCheck: 'mismatch' });
  check(!!c.synthesis && !S.synthesisEligible(c), '統合は採用されるが、確認するまで使えない');
  S.setConfirmation(c, S.resultAttemptId(c, 'SYN', c.synthesis, null), S.RUNNER_UNVERIFIED, 'confirm', t());
  check(S.synthesisEligible(c), '確認した後は使える');
});
test('RN-B5 実行係を通らない返事（手動・貼り付け）は今までどおり', () => {
  const c = newCase(), task = 'CASE-T-OBS-GPT';
  const a = recv(c, R('GPT', task), task);
  check(!('runner' in a) && !('runnerCheck' in a), '項目を足さない');
  check(S.eligibleRecord(c, 'RESULT', c.tasks.GPT.result, 'GPT'), '利用可能');
});

  return results;
}
