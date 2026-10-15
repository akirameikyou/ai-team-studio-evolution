'use strict';
// AI Team Studio — stage 1b state rules (contract: STUDIO-PLAN_1b実装契約と受入条件_案v0.3.txt) + stage 1c request records.
// Pure functions over a Case object: receiving, adopting, generations, revisions, staleness, guards, confirmations.
// No DOM, no storage, no network. Depends on ParseV2 (parse_v2.js).
// Reading never writes: defaults for old Cases are applied in the getters, so a stored old Case is not rewritten until a new event happens.
(function (global) {
  const P = () => global.ParseV2;
  const KEYS = {
    RESULT: ['CASE', 'TASK', 'FROM', 'SOURCE_ACCESS', 'CONDITION', 'OBSERVATION', 'EVIDENCE', 'UNKNOWN'],
    SYNTH: ['CASE', 'TASK', 'FROM', 'COMMON', 'DIFFERENCES', 'UNKNOWN', 'HOLD', 'EXECUTION_NEEDS', 'NEXT_TASK', 'GATE_QUESTION'],
    DONE: ['CASE', 'TASK', 'FROM', 'CHANGED', 'VERIFIED', 'UNKNOWN', 'NEXT']
  };
  // R-2 / D12 = A: fixed priority.
  const TAGS = [['SYN', 'STUDIO_SYNTHESIS', KEYS.SYNTH], ['DONE', 'STUDIO_DONE', KEYS.DONE], ['RESULT', 'STUDIO_RESULT', KEYS.RESULT]];
  const ADOPTABLE = ['OK', 'PARTIAL', 'UNCONFIRMED'];
  let counter = 0;
  const newId = (p, now) => p + Date.parse(now).toString(36) + (++counter).toString(36) + Math.random().toString(36).slice(2, 6);

  // ---------- read-only getters with defaults (L-1) ----------
  const attempts = c => c.parseAttempts || [];
  const obsRevision = c => c.obsRevision || 0;
  const synRevision = c => c.synRevision || 0;
  const confirmations = c => c.confirmations || [];
  const cleanName = name => String(name).replace(/\W+/g, '');
  const obsGen = (c, name) => (c.generations && c.generations.obs && c.generations.obs[name]) || 1;
  const synGen = c => (c.generations && c.generations.SYN) || 1;
  const nextGen = c => (c.generations && c.generations.NEXT) || 1;
  // G-1/G-2: generation 1 keeps the old ID form, so old Cases and new ones agree; later generations add "-<n>".
  const withGen = (base, n) => n <= 1 ? base : base + '-' + n;
  const obsTaskId = (c, name, n = obsGen(c, name)) => withGen(c.id + '-OBS-' + cleanName(name), n);
  const synTaskId = (c, n = synGen(c)) => withGen(c.id + '-SYN', n);
  const nextTaskId = (c, n = nextGen(c)) => withGen(c.id + '-NEXT', n);
  const taskGen = (task, base) => { if (task === base) return 1; const m = String(task).match(/^(.*)-(\d+)$/); return m && m[1] === base ? +m[2] : null; };
  // L-2: virtual attempt IDs for results stored before 1b (never written back).
  const resultAttemptId = (c, kind, rec, name) => rec ? (rec.attemptId || ('legacy:' + c.id + ':' + (kind === 'RESULT' ? name : kind) + ':' + rec.at)) : null;
  const gateSynthesisAttemptId = (c, g) => g.synthesisAttemptId || ('legacy:' + c.id + ':SYN:' + g.synthesisAt);

  // ---------- confirmations (K-1, K-2) ----------
  function confirmedItems(c, attemptId) {
    const last = {};
    for (const e of confirmations(c)) if (e.attemptId === attemptId) last[e.item] = e.action;
    return Object.keys(last).filter(k => last[k] === 'confirm');
  }
  function eligibleRecord(c, kind, rec, name, opts = {}) {
    if (!rec) return false;
    if (!rec.status) return true;                                   // old format
    const id = resultAttemptId(c, kind, rec, name);
    return P().eligible({ ...rec, confirmation: { items: confirmedItems(c, id) } }, opts);
  }
  function pendingConfirmations(c, kind, rec, name, opts = {}) {
    if (!rec || !rec.status) return [];
    const done = new Set(confirmedItems(c, resultAttemptId(c, kind, rec, name)));
    return P().requiredConfirmations(rec, opts).filter(x => !done.has(x));
  }

  // ---------- counts ----------
  const eligibleObservers = (c, opts) => c.observers.filter(n => eligibleRecord(c, 'RESULT', c.tasks[n].result, n, opts));
  function receivedCount(c, name) {
    const n = attempts(c).filter(a => a.assignment && a.assignment.kind === 'RESULT' && a.assignment.name === name).length;
    return n || (c.tasks[name].result && !c.tasks[name].result.attemptId ? 1 : 0);   // an old result counts as one receipt
  }

  // ---------- staleness (§5 V-3) and guards (V-4) ----------
  function synthesisStale(c) {
    if (!c.synthesisReq) return false;
    if ((c.synthesisReq.obsRevision || 0) !== obsRevision(c)) return true;
    return !!c.synthesisReq.task && c.synthesisReq.task !== synTaskId(c);
  }
  const synthesisEligible = (c, opts) => !!c.synthesis && eligibleRecord(c, 'SYN', c.synthesis, null, opts);
  function gateStale(c) {
    const g = c.gate; if (!g) return false;
    if (!c.synthesis || synthesisStale(c)) return true;
    if (gateSynthesisAttemptId(c, g) !== resultAttemptId(c, 'SYN', c.synthesis)) return true;
    if ((g.synRevision || 0) !== synRevision(c)) return true;
    return !synthesisEligible(c);
  }
  function nextStale(c) {
    const n = c.next; if (!n) return false;
    if (!c.gate || gateStale(c) || c.gate.decision !== 'APPROVE') return true;
    return (n.gateId || null) !== (c.gate.id || null);
  }
  function doneStale(c) {
    const d = c.done; if (!d) return false;
    if (!c.next || nextStale(c)) return true;
    return (d.nextTask || c.next.task || null) !== (c.next.task || null);
  }
  const canRequestSynthesis = c => eligibleObservers(c).length >= 1;
  const canGate = c => !!c.synthesis && !synthesisStale(c) && synthesisEligible(c);
  const canSendNext = c => !!c.gate && c.gate.decision === 'APPROVE' && !gateStale(c);
  const canCountDone = c => !!c.done && !doneStale(c) && eligibleRecord(c, 'DONE', c.done);
  function staleReason(c, what) {
    if (what === 'SYN' && synthesisStale(c)) return '統合の依頼後に観測が変わった（採用の切り替え・確認・取り消し・追加）';
    if (what === 'GATE' && gateStale(c)) return synthesisStale(c) ? '判断した統合が古い' : !synthesisEligible(c) ? '統合が使える状態でない（確認待ち等）' : '統合が新しくなった、または確認が変わった';
    if (what === 'NEXT' && nextStale(c)) return 'もとの判断（GATE）が古い、または新しい判断がある';
    if (what === 'DONE' && doneStale(c)) return 'もとの実行タスク（NEXT）が古い';
    return '';
  }

  // ---------- revisions (V-1) ----------
  const bump = (c, which) => { if (which === 'obs') c.obsRevision = obsRevision(c) + 1; else c.synRevision = synRevision(c) + 1; };

  // ---------- receiving (R-1 .. R-6) ----------
  function parseAll(text) {
    const out = {};
    for (const [k, tag, keys] of TAGS) out[k] = P().parseBlockV2(text, tag, keys);
    return out;
  }
  // Which current task does a picked block answer? Returns {kind, name, task} | {late: true, ...} | null (unknown).
  function resolveTarget(c, kind, fields, expectedTask) {
    const task = String((fields && fields.TASK) || '').trim();
    if (kind === 'SYN' || kind === 'DONE') {
      const base = kind === 'SYN' ? c.id + '-SYN' : c.id + '-NEXT', cur = kind === 'SYN' ? synTaskId(c) : nextTaskId(c);
      const t = task || expectedTask || '';
      if (t) { if (t === cur) return { kind, task: cur }; const g = taskGen(t, base); return g ? { late: true, kind, task: t } : null; }
      const gen = kind === 'SYN' ? synGen(c) : nextGen(c);
      return gen === 1 ? { kind, task: cur } : null;                                     // G-4
    }
    const t = task || expectedTask || '';
    if (t) {
      for (const n of c.observers) {
        if (t === obsTaskId(c, n)) return { kind, name: n, task: t };
        if (taskGen(t, c.id + '-OBS-' + cleanName(n))) return { late: true, kind, name: n, task: t };
      }
      return null;
    }
    const from = String((fields && fields.FROM) || '').toLowerCase();
    if (!from) return null;
    const n = c.observers.find(x => x.toLowerCase() === from) || c.observers.find(x => from.includes(x.toLowerCase()) || x.toLowerCase().includes(from));
    return n && obsGen(c, n) === 1 ? { kind, name: n, task: obsTaskId(c, n) } : null;     // FROM only when generation 1
  }

  // Adopt the attempt's parsed block into the target task (§3). Returns a short outcome string.
  function adopt(c, a, kind, name, now) {
    const rec = kind === 'RESULT' && a.parsed.RESULT === null ? { status: 'NO_BLOCK', reason: null, fields: {}, missing: KEYS.RESULT.slice(), warnings: [], conflicts: [], selected: null, candidates: [], ruleVersion: P().RULE_VERSION, templateTableVersion: P().TEMPLATE_TABLE_VERSION } : a.parsed[kind];
    const base = { ...rec, raw: a.raw, at: now, via: a.via, attemptId: a.attemptId };
    if (kind === 'RESULT') {
      const t = c.tasks[name], cur = t.result;
      if (rec.status === 'NO_BLOCK' && cur && cur.status !== 'NO_BLOCK') return 'kept';             // A-3: never replace a structured or old result with NO_BLOCK
      t.result = { ...base, format: rec.status === 'NO_BLOCK' ? '形式外' : rec.missing.length ? '一部' : '完全' };
      t.adoptedAttemptId = a.attemptId;
      if (!t.sentAt) t.sentAt = now;
      bump(c, 'obs');
      return 'adopted';
    }
    if (kind === 'SYN') {
      if (!c.synthesisReq || rec.status === 'NO_BLOCK') return 'rejected';
      c.synthesis = { ...base, by: (rec.fields.FROM || c.synthesisReq.to), task: c.synthesisReq.task || synTaskId(c) };
      bump(c, 'syn');
      return 'adopted';
    }
    if (!c.next || rec.status === 'NO_BLOCK') return 'rejected';
    if (nextStale(c)) return 'stale';                                                               // a report on a stale NEXT is kept in history only (A-07)
    c.done = { ...base, by: rec.fields.FROM || c.next.to, nextTask: c.next.task || nextTaskId(c) };
    return 'adopted';
  }

  // One receipt (paste / file / relay / API). meta = { via, now, rawSha256, expectedTask }.
  function receive(c, text, meta) {
    const now = meta.now, parsed = parseAll(text);
    const a = { attemptId: newId('A', now), at: now, via: meta.via, raw: text, rawSha256: meta.rawSha256 || null, expectedTask: meta.expectedTask || null,
                parsed, ruleVersion: P().RULE_VERSION, templateTableVersion: P().TEMPLATE_TABLE_VERSION, picked: null, notAdopted: [], assignment: null, outcome: null };
    (c.parseAttempts = c.parseAttempts || []).push(a);                                            // R-1: record first
    const usable = TAGS.filter(([k]) => parsed[k] && ADOPTABLE.includes(parsed[k].status)).map(([k]) => k);
    if (usable.length) {
      a.picked = usable[0];
      a.notAdopted = usable.slice(1).map(k => ({ tag: k, status: parsed[k].status, reason: parsed[k].reason, why: '優先順で後（SYNTHESIS→DONE→RESULT）' }));
      const target = resolveTarget(c, a.picked, parsed[a.picked].fields, meta.expectedTask);
      if (!target) { a.outcome = 'unassigned'; return a; }
      if (target.late) { a.outcome = 'late'; a.lateTask = target.task; return a; }                 // G-3: keep, never adopt
      a.assignment = { kind: target.kind, name: target.name || null, task: target.task, at: now, mode: 'auto' };
      a.outcome = adopt(c, a, target.kind, target.name, now);
      return a;
    }
    const failed = TAGS.filter(([k]) => parsed[k] && parsed[k].status === 'PARSE_FAILED').map(([k]) => k);
    a.outcome = failed.length ? 'failed' : 'noblock';                                              // R-3 / R-4
    if (meta.expectedTask) {
      const target = resolveTarget(c, 'RESULT', null, meta.expectedTask) || resolveTarget(c, 'SYN', null, meta.expectedTask) || resolveTarget(c, 'DONE', null, meta.expectedTask);
      if (target && !target.late) {
        a.assignment = { kind: target.kind, name: target.name || null, task: target.task, at: now, mode: 'auto' };
        if (a.outcome === 'noblock' && target.kind === 'RESULT') a.outcome = adopt(c, a, 'RESULT', target.name, now);
      }
    }
    return a;
  }

  // R-6: assign an unassigned receipt to a task of the current generation. No new attempt; never confirms or approves anything.
  function assign(c, attemptId, target, now, by) {
    const a = attempts(c).find(x => x.attemptId === attemptId);
    if (!a) return { ok: false, why: '受信が見つかりません' };
    if (a.assignment) return { ok: false, why: 'この受信は割り当て済みです（二重割り当ては不可）' };
    const kind = target.kind, name = target.name || null;
    // D12 holds for manual assignment too: when a block was picked by priority, only its own kind may be assigned
    // (assignment only supplies the missing destination). Lower-priority blocks recorded in notAdopted can never be adopted this way.
    if (a.picked && a.picked !== kind) return { ok: false, why: 'この受信で優先された「' + ({ SYN: '統合', DONE: '完了報告', RESULT: '観測' })[a.picked] + '」以外には割り当てられません（D12）' };
    // A record with an adoptable block but no picked tag cannot come from receive(); it only arrives via an edited or
    // inconsistent JSON file. Refuse it, so D12 cannot be bypassed through import (Codex C40 b).
    if (!a.picked && TAGS.some(([k]) => a.parsed[k] && ADOPTABLE.includes(a.parsed[k].status))) return { ok: false, why: 'この受信の記録は整合していません（採用できるタグがあるのに優先タグがありません）。割り当てできません' };
    const task = kind === 'RESULT' ? obsTaskId(c, name) : kind === 'SYN' ? synTaskId(c) : nextTaskId(c);
    if (kind === 'RESULT' && !c.observers.includes(name)) return { ok: false, why: '観測者がいません' };
    if (kind === 'SYN' && !c.synthesisReq) return { ok: false, why: '統合をまだ依頼していません' };
    if (kind === 'DONE' && !c.next) return { ok: false, why: '実行タスクをまだ送っていません' };
    a.assignment = { kind, name, task, at: now, mode: 'manual', by: by || 'screen' };
    let outcome = 'assigned';
    const block = a.parsed[kind];
    if (a.picked && block && ADOPTABLE.includes(block.status)) outcome = adopt(c, a, kind, name, now);
    else if (kind === 'RESULT' && TAGS.every(([k]) => a.parsed[k] === null)) outcome = adopt(c, a, 'RESULT', name, now);
    a.assignmentOutcome = outcome;
    return { ok: true, outcome };
  }
  const unassigned = c => attempts(c).filter(a => !a.assignment && a.outcome !== 'late');

  // K-1..K-3: confirm / revoke one item of one attempt (append only). Bumps the revision if that attempt is in use.
  function setConfirmation(c, attemptId, item, action, now, by) {
    (c.confirmations = c.confirmations || []).push({ eventId: newId('K', now), attemptId, item, action, at: now, by: by || 'screen' });
    if (c.observers.some(n => resultAttemptId(c, 'RESULT', c.tasks[n].result, n) === attemptId)) bump(c, 'obs');
    if (c.synthesis && resultAttemptId(c, 'SYN', c.synthesis) === attemptId) bump(c, 'syn');
  }

  // ---------- requests ----------
  const archive = (c, reason, now) => {
    const items = [['synthesis', c.synthesis], ['gate', c.gate], ['next', c.next], ['done', c.done]].filter(([, v]) => v);
    if (items.length || c.synthesisReq) (c.archive = c.archive || []).push({ at: now, reason, synthesisReq: c.synthesisReq || null, ...Object.fromEntries(items) });
    c.synthesis = null; c.gate = null; c.next = null; c.done = null;
  };
  function startSynthesisRequest(c, to, now) {
    if (!canRequestSynthesis(c)) return { ok: false, why: '使える観測が0件です' };
    // A new generation whenever any synthesis was requested before (an old Case's single request counts as generation 1).
    const had = (c.generations && c.generations.SYN) || (c.synthesisReq || c.synthesis ? 1 : 0);
    const gen = had + 1;
    if (had) archive(c, '統合をやり直したため（世代 ' + gen + '）', now);
    (c.generations = c.generations || {}).SYN = gen;
    const inputs = eligibleObservers(c).map(n => resultAttemptId(c, 'RESULT', c.tasks[n].result, n));
    c.synthesisReq = { to, task: synTaskId(c, gen), gen, obsRevision: obsRevision(c), inputAttemptIds: inputs, at: now };
    return { ok: true, task: c.synthesisReq.task };
  }
  function recordGate(c, decision, reason, now) {
    if (!canGate(c)) return { ok: false, why: staleReason(c, 'SYN') || '統合が使える状態ではありません（確認待ち・未着）' };
    c.gate = { id: newId('G', now), decision, reason, at: now, synthesisAt: c.synthesis.at, synthesisAttemptId: resultAttemptId(c, 'SYN', c.synthesis), synRevision: synRevision(c) };
    (c.gateHistory = c.gateHistory || []).push(c.gate);
    return { ok: true };
  }
  function startNext(c, to, packet, now) {
    if (!canSendNext(c)) return { ok: false, why: staleReason(c, 'GATE') || 'APPROVE された有効な判断がありません' };
    const had = (c.generations && c.generations.NEXT) || (c.next ? 1 : 0);
    const gen = had + 1;
    if (c.next) { (c.archive = c.archive || []).push({ at: now, reason: '実行タスクを送り直したため', next: c.next, done: c.done || null }); c.done = null; }
    (c.generations = c.generations || {}).NEXT = gen;
    c.next = { id: newId('N', now), to, packet, task: nextTaskId(c, gen), gen, gateId: c.gate.id || null, at: now };
    return { ok: true, task: c.next.task };
  }
  function startObservationResend(c, name) {
    const g = c.generations = c.generations || {}; g.obs = g.obs || {};
    g.obs[name] = obsGen(c, name) + 1;
    c.tasks[name].taskId = obsTaskId(c, name);
    return c.tasks[name].taskId;
  }

  // ---------- stage 1c: request records (append only) ----------
  // Q-1: every generated request text is stored once, with its Task ID, generation and SHA-256, before it is sent.
  // Q-2: each delivery attempt is appended to that record. Showing or copying the stored text again changes nothing.
  // Q-3: a request whose delivery failed can be sent again with the same Task ID and the same text (no new generation).
  // Reading never writes: an old Case without records simply has no current request.
  const requests = c => c.requests || [];
  const DELIVERED = ['copied', 'shown', 'queued', 'answered'];
  function recordRequest(c, r) {
    const req = { requestId: newId('Q', r.at), kind: r.kind, name: r.name || null, task: r.task, gen: r.gen || 1, packet: r.packet, sha256: r.sha256 || null, at: r.at, deliveries: [] };
    (c.requests = c.requests || []).push(req);
    return req;
  }
  function recordDelivery(c, requestId, d) {
    const req = requests(c).find(x => x.requestId === requestId);
    if (!req) return { ok: false, why: '依頼記録が見つかりません' };
    req.deliveries.push({ at: d.at, status: d.status, connector: d.connector || null, error: d.error || null, retry: !!d.retry });
    return { ok: true };
  }
  const deliveryState = req => !req ? null : req.deliveries.some(d => DELIVERED.includes(d.status)) ? 'delivered' : req.deliveries.some(d => d.status === 'failed') ? 'failed' : 'unsent';
  function currentTask(c, kind, name) {
    if (kind === 'OBS') return c.tasks && c.tasks[name] ? c.tasks[name].taskId : null;
    if (kind === 'SYN') return c.synthesisReq ? (c.synthesisReq.task || synTaskId(c)) : null;
    if (kind === 'NEXT') return c.next ? (c.next.task || nextTaskId(c)) : null;
    return null;
  }
  function currentRequest(c, kind, name) {
    const task = currentTask(c, kind, name); if (!task) return null;
    const list = requests(c).filter(r => r.kind === kind && r.task === task && (kind !== 'OBS' || r.name === name));
    return list.length ? list[list.length - 1] : null;
  }
  const canRetry = (c, kind, name) => deliveryState(currentRequest(c, kind, name)) === 'failed';

  global.StudioState = {
    KEYS, attempts, obsRevision, synRevision, confirmedItems, eligibleRecord, pendingConfirmations, eligibleObservers, receivedCount,
    synthesisStale, gateStale, nextStale, doneStale, staleReason, canRequestSynthesis, canGate, canSendNext, canCountDone, synthesisEligible,
    obsTaskId, synTaskId, nextTaskId, obsGen, synGen, nextGen, resultAttemptId,
    receive, assign, unassigned, setConfirmation, startSynthesisRequest, recordGate, startNext, startObservationResend,
    requests, recordRequest, recordDelivery, deliveryState, currentRequest, canRetry
  };
})(typeof globalThis !== 'undefined' ? globalThis : this);
