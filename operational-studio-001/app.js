'use strict';
// AI Team Studio — 実運用版 001
// Flow: question -> CASE -> SOURCE -> OBSERVE (independent) -> DIFFERENCE -> SYNTHESIS -> HUMAN GATE -> NEXT
// The human writes the question and makes the gate decision. Everything else is prepared by the Studio.

const KEY = 'ats-operational-001';
const STAGES = [['CASE', '問い'], ['SOURCE', '資料'], ['OBSERVE', '観測'], ['DIFFERENCE', '差分'], ['SYNTHESIS', '統合'], ['GATE', '判断'], ['NEXT', '次へ']];
const ACCESS = ['DIRECT', 'READABLE', 'INACCESSIBLE', 'UNKNOWN'];
const ORIGINS = StudioPackets.ORIGINS;
const DEFAULT_OBSERVERS = [
  { name: 'GPT', connector: 'manual', route: 'api:openai' },
  { name: 'Claude Code', connector: 'manual', route: 'agent' },
  { name: 'Gemini', connector: 'manual', route: 'api:gemini' },
  { name: 'Codex', connector: 'manual', route: 'agent' },
  { name: 'Meta', connector: 'manual', route: 'manual' }
];

let store = { version: 1, cases: {}, order: [], currentId: null, settings: { proxyUrl: '', observers: DEFAULT_OBSERVERS.map(o => ({ ...o })) } };
let saveBlocked = false, pollTimer = null;
const $ = id => document.getElementById(id);
// In-page replacements for confirm()/alert(): some embedded browsers ignore the native dialogs.
function ask(msg, okLabel = 'はい') {
  return new Promise(res => {
    const d = document.createElement('dialog');
    d.innerHTML = `<p style="white-space:pre-wrap;max-width:440px">${esc(msg)}</p><div class="row end"><button class="btn" value="no">やめる</button><button class="btn primary" value="ok">${esc(okLabel)}</button></div>`;
    document.body.appendChild(d); d.showModal();
    d.querySelectorAll('button').forEach(b => b.onclick = () => { d.close(); d.remove(); res(b.value === 'ok'); });
    d.addEventListener('cancel', () => { d.remove(); res(false); });
  });
}
function tell(msg) { return ask(msg, 'OK').then(() => {}); }
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const now = () => new Date().toISOString();
const hm = StudioPackets.hm;
const cur = () => store.cases[store.currentId] || null;
const obsSetting = name => store.settings.observers.find(o => o.name === name) || { name, connector: 'manual', route: 'manual' };

// ---------- persistence (never overwrite an unreadable value) ----------
function load() {
  let raw = null;
  try { raw = localStorage.getItem(KEY); } catch (e) { return block('保存領域を読めません。上書きを防ぐため保存を止めています。'); }
  if (raw == null) return;
  try {
    const o = JSON.parse(raw);
    if (!o || o.version !== 1 || typeof o.cases !== 'object') throw new Error('format');
    store = o;
  } catch (e) { block('保存データを読めません。上書きを防ぐため保存を止めています（記録のJSON読み込みで復旧できます）。'); }
}
// Returns whether the store was written. Callers that must not continue without a stored record (Q-1) check it.
function save() {
  if (saveBlocked) return false;
  const prevRev = store.rev || 0;
  store.rev = prevRev + 1;    // 2-C: a revision number for the record copies
  try { localStorage.setItem(KEY, JSON.stringify(store)); backup.saveFailed = false; scheduleBackup(); return true; }
  catch (e) { store.rev = prevRev; backup.saveFailed = true; flash('保存に失敗しました（' + e.name + '）。記録をJSONで保存してください。', true); return false; }
}

// ---------- 2-C: copies of the record in the relay folder ----------
// After a successful save, and 3 s after the last one, the text actually stored in localStorage (never an unsaved
// state) is sent to the relay, which keeps every copy as its own file. One copy is in flight at a time; a save during
// it queues one more. Nothing is sent while saving is blocked. Failures are shown, never hidden.
// Copies run strictly one after another (a promise chain); runBackup() resolves with the result of *its own* copy,
// so a caller that must know whether the current record was kept (restore) waits for exactly that copy.
// At most one copy waits behind the one in flight: a copy reads localStorage when it starts, so a second waiting copy
// would only repeat it (Codex review of b061274). Each request gives up after timeoutMs, so the chain cannot hang.
const BACKUP_DELAY = 3000;
const backup = { timer: null, chain: Promise.resolve(), queued: null, timeoutMs: 20000, last: null, error: null, count: null, totalBytes: null, saveFailed: false };
function scheduleBackup() {
  if (!store.settings.proxyUrl) return renderBackupNote();
  clearTimeout(backup.timer); backup.timer = setTimeout(runBackup, BACKUP_DELAY);
}
function runBackup() {
  if (backup.queued) return backup.queued;
  const p = backup.chain.then(() => { backup.queued = null; return copyOnce(); });
  backup.queued = p;
  backup.chain = p.catch(() => {});
  return p;
}
async function copyOnce() {
  if (saveBlocked || !store.settings.proxyUrl) { renderBackupNote(); return { ok: false, skipped: true, why: saveBlocked ? '保存が止まっています' : '中継がありません' }; }
  let text = null;
  try { text = localStorage.getItem(KEY); } catch (e) { backup.error = { at: now(), why: '保存領域を読めません' }; renderBackupNote(); return { ok: false, why: backup.error.why }; }
  if (text == null) { renderBackupNote(); return { ok: false, skipped: true, why: 'まだ保存された記録がありません' }; }
  let rev = 0; try { rev = JSON.parse(text).rev || 0; } catch (_) { }      // the revision of the text being sent, not of a later save
  const ac = new AbortController(), timer = setTimeout(() => ac.abort(), backup.timeoutMs);
  try {
    const r = await fetch(store.settings.proxyUrl.replace(/\/$/, '') + '/snapshot', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ rev, store: text }), signal: ac.signal })
      .catch(e => { throw ac.signal.aborted ? new Error(`中継が ${backup.timeoutMs / 1000} 秒以内に応答しません`) : e; });
    const j = await r.json();
    if (!r.ok || !j.saved) throw new Error(j.error || 'HTTP ' + r.status);
    backup.last = { at: now(), name: j.saved, rev }; backup.error = null;
    if (typeof j.count === 'number') { backup.count = j.count; backup.totalBytes = j.totalBytes; }
    renderBackupNote(); return { ok: true, name: j.saved };
  } catch (e) { backup.error = { at: now(), why: e.message }; renderBackupNote(); return { ok: false, why: e.message }; }
  finally { clearTimeout(timer); }
}
function renderBackupNote() {
  const el = $('backupNote'); if (!el) return;
  el.className = backup.error ? 'ng' : '';
  const size = backup.count != null ? `・写し ${backup.count}件 ${Math.ceil((backup.totalBytes || 0) / 1024)} KB` : '';
  el.textContent = !store.settings.proxyUrl ? 'なし（中継なし）' : backup.error ? `失敗 ${hm(backup.error.at)}（${backup.error.why}）` : backup.last ? `${hm(backup.last.at)}（r${backup.last.rev}${size}）` : '中継へ保存します';
}
async function restoreFromCopy() {
  if (!store.settings.proxyUrl) return tell('記録の写しは中継のフォルダにあります。先に「AIの接続設定」で中継のURLを入れてください。');
  const base = store.settings.proxyUrl.replace(/\/$/, '');
  let list;
  try { list = await (await fetch(base + '/snapshots')).json(); } catch (e) { return tell('中継に届きません（' + e.message + '）'); }
  if (!list.snapshots || !list.snapshots.length) return tell('記録の写しはまだありません。');
  const name = await pickCopy(list);
  if (!name) return;
  let text;
  try { text = (await (await fetch(base + '/snapshot?' + new URLSearchParams({ name }))).json()).text; } catch (e) { return tell('写しを読めません（' + e.message + '）'); }
  const p = StudioText.parseStoreSnapshot(text);
  if (p.error) return tell('この写しは使えません：' + p.error);
  const nowCount = Object.keys(store.cases).length, newCount = Object.keys(p.store.cases).length;
  // Copies are made from what was saved. After a failed save the screen holds more than that, and it would be lost.
  const unsaved = backup.saveFailed ? '\n\n注意：最後の保存に失敗しています。画面に出ている今の内容のうち、保存できなかった分は写しに入りません。必要なら先に「記録をJSONで保存」で書き出してください。' : '';
  if (!(await ask(`今の記録（Case ${nowCount}件）を、写し ${name}（Case ${newCount}件）で置き換えます。\n今の記録は、先に写しとして保存します。${unsaved}`, '置き換える'))) return;
  // Keep the current record as one more copy first, whatever it holds (settings count too, even with 0 Cases). The
  // copy waits for any copy already in flight and reports its own result.
  if (!saveBlocked) {
    clearTimeout(backup.timer);
    const kept = await runBackup();
    if (!kept.ok && !kept.skipped && !(await ask('今の記録の写しを保存できませんでした（' + kept.why + '）。それでも置き換えますか？', '置き換える'))) return;
  }
  if (saveBlocked) { if (!(await ask('保存が止まっています。写しの内容で保存を再開しますか？（読めなかった保存データは上書きされます）'))) return; saveBlocked = false; $('saveWarn').hidden = true; }
  const prev = store;
  store = p.store;
  if (!save()) { store = prev; render(); return tell('戻した記録を保存できませんでした。今の記録はそのままです（画面の警告を確認してください）。'); }
  render(); schedulePoll();
  tell(`写し ${name} から戻しました（Case ${newCount}件）。`);
}
function pickCopy(list) {
  return new Promise(res => {
    const d = document.createElement('dialog');
    d.innerHTML = `<p>戻す写しを選んでください（新しい順。全 ${list.total} 件のうち ${list.snapshots.length} 件）</p><p class="hint">${esc(list.dir || '')}</p>
      <select id="copyPick" size="8" style="min-width:420px">${list.snapshots.map((s, i) => `<option value="${esc(s.name)}"${i ? '' : ' selected'}>${esc(s.name)}（${Math.ceil(s.bytes / 1024)} KB）</option>`).join('')}</select>
      <div class="row end"><button class="btn" value="no">やめる</button><button class="btn primary" value="ok">この写しを使う</button></div>`;
    document.body.appendChild(d); d.showModal();
    d.querySelectorAll('button').forEach(b => b.onclick = () => { const v = d.querySelector('#copyPick').value; d.close(); d.remove(); res(b.value === 'ok' ? v : null); });
    d.addEventListener('cancel', () => { d.remove(); res(null); });
  });
}
function block(msg) { saveBlocked = true; setTimeout(() => flash(msg, true), 0); }
// Save problems get their own banner at the top of the page (render() never overwrites it).
function flash(msg, bad) { if (bad) { const w = $('saveWarn'); w.textContent = msg; w.hidden = false; } }
function log(c, t) { c.log.push({ at: now(), t }); }
function bump(c, k, n = 1) { c.metrics[k] = (c.metrics[k] || 0) + n; }

// ---------- case creation ----------
// 2-B: date + counter + random suffix (StudioText.caseIdFor). When a relay is set, an ID whose folder already exists
// there is not used. If the relay cannot be asked, the random suffix alone keeps the ID apart (logged by the caller).
function randomSuffix() {
  // Uniform over 36 characters: bytes 252..255 are rejected (252 = 36 × 7), so every character is equally likely.
  const A = '0123456789abcdefghijklmnopqrstuvwxyz', out = [];
  while (out.length < 6) { const b = new Uint8Array(12); crypto.getRandomValues(b); for (const x of b) if (x < 252 && out.length < 6) out.push(A[x % 36]); }
  return out.join('');
}
async function newCaseId() {
  for (let i = 0; i < 5; i++) {
    const id = StudioText.caseIdFor(new Date(), Object.keys(store.cases), randomSuffix());
    if (store.cases[id]) continue;
    if (!store.settings.proxyUrl) return { id, checked: false };
    try {
      const j = await (await fetch(store.settings.proxyUrl.replace(/\/$/, '') + '/case-exists?' + new URLSearchParams({ case: id }))).json();
      if (j.exists === false) return { id, checked: true };
      if (j.exists !== true) return { id, checked: false };
    } catch (e) { return { id, checked: false }; }
  }
  return { id: null };
}
// 2-1A: the premise is added before anything is sent, because relay-connected AIs get their task at creation.
async function createCase(question, observerNames, premise) {
  const pre = premise && premise.text.trim() ? await makeTextSource('S1', premise.title, premise.text) : null;
  if (pre && pre.error) return tell(pre.error);
  const nid = await newCaseId();
  if (!nid.id) return tell('重ならない Case 番号を作れませんでした。もう一度「始める」を押してください。');
  const c = {
    id: nid.id, question: question.trim(), createdAt: now(), sources: StudioText.extractSources(question, { bareNames: false }),   // 1d-1: file names in the question are not SOURCEs
    observers: observerNames, tasks: {}, synthesis: null, gate: null, gateHistory: [], next: null,
    metrics: { typed: 1 }, log: []
  };
  if (pre) { c.sources.unshift(pre); c.sources.forEach((s, i) => { s.id = 'S' + (i + 1); }); }
  log(c, `問いを受け取り、CASEを作成（資料 ${c.sources.length}件${pre ? '・うち前提資料の本文 1件' : '・本文から取り出し'}）`);
  if (store.settings.proxyUrl && !nid.checked) log(c, 'Case 番号が中継のフォルダと重ならないかは確かめられませんでした（乱数の部分で区別しています）');
  for (const name of observerNames) c.tasks[name] = { taskId: c.id + '-OBS-' + name.replace(/\W+/g, ''), sentAt: null, sendStatus: null, result: null };
  store.cases[c.id] = c; store.order.unshift(c.id); store.currentId = c.id;
  save(); render();
  verifySources(c);         // automatic, no human step
  autoDispatch(c);          // relay-connected AIs get their task immediately
}

// ---------- SOURCE ----------
async function sha256Hex(buf) {
  if (!(window.crypto && crypto.subtle)) return null;
  return [...new Uint8Array(await crypto.subtle.digest('SHA-256', buf))].map(b => b.toString(16).padStart(2, '0')).join('');
}
async function verifySources(c) {
  for (const s of c.sources) {
    if (s.verified || !/^https?:/.test(s.ref)) continue;
    try {
      const r = await fetch(s.ref, { cache: 'no-store' }); if (!r.ok) throw new Error('HTTP ' + r.status);
      const buf = await r.arrayBuffer(); const h = await sha256Hex(buf);
      if (h) { s.verified = { sha256: h, size: buf.byteLength, by: 'Studio（ブラウザから取得）', at: now() }; log(c, `${s.id} をブラウザから取得して確認（${buf.byteLength} bytes）`); }
    } catch (e) {
      if (store.settings.proxyUrl) {
        try { const j = await (await fetch(store.settings.proxyUrl.replace(/\/$/, '') + '/hash?' + new URLSearchParams({ url: s.ref }))).json();
          if (j.sha256) { s.verified = { sha256: j.sha256, size: j.size, by: 'Studio（ローカル中継で取得）', at: now() }; log(c, `${s.id} をローカル中継経由で確認`); continue; } } catch (_) { }
      }
      s.verifyNote = 'Studioからは取得できず（' + e.message + '）。存在は未確認'; log(c, `${s.id} はStudioから取得できず — 未確認のまま`);
    }
  }
  save(); render();
}
async function makeTextSource(id, title, text) {
  const bytes = new TextEncoder().encode(String(text));
  return StudioText.textSource(id, title, text, await sha256Hex(bytes), bytes.length, now());
}
function srcState(s) {
  if (s.verified && s.claimedHash) return s.verified.sha256 === s.claimedHash ? ['確認済み・申告と一致', 'ok'] : ['確認済み・申告と不一致', 'ng'];
  if (s.verified) return ['確認済み', 'ok'];
  if (s.claimedHash) return ['申告のみ（未確認）', 'wait'];
  return ['未確認', 'wait'];
}

// ---------- packets ---------- (moved to studio_packets.js in stage 2; same names kept here)
const { sourceLines, observe: observePacket, synthesis: synthesisPacket, next: nextPacket } = StudioPackets;
const oneLine = StudioPackets.oneLine;

function parseAccess(v) {
  const out = {}; for (const m of String(v || '').matchAll(/(S\d+)\s*=\s*([A-Za-z]+)([^,、]*)/g)) {
    const st = m[2].toUpperCase(); out[m[1]] = { status: ACCESS.includes(st) ? st : 'UNKNOWN', note: m[3].trim().replace(/^[（(]|[)）]$/g, '') };
  } return out;
}
// ---------- receiving (stage 1b: every receipt is recorded first, then adopted by StudioState rules) ----------
function updateAccess(c, name) {
  const r = c.tasks[name].result; if (!r) return;
  const acc = parseAccess(r.fields.SOURCE_ACCESS);
  for (const s of c.sources) s.access[name] = acc[s.id] || { status: 'UNKNOWN', note: r.status === 'NO_BLOCK' ? '形式外の回答' : '記載なし' };
}
const TAG_LABEL = { SYN: '統合', DONE: '完了報告', RESULT: '観測' };
function targetLabel(a) { const x = a.assignment; return x ? (x.kind === 'RESULT' ? x.name + ' の観測' : TAG_LABEL[x.kind]) : '宛先不明'; }
function describeAttempt(a) {
  const pick = a.picked && a.parsed[a.picked];
  const fails = Object.entries(a.parsed).filter(([, r]) => r && r.status === 'PARSE_FAILED').map(([k, r]) => TAG_LABEL[k] + ':' + r.reason);
  let s;
  switch (a.outcome) {
    case 'adopted': s = `${targetLabel(a)}として採用（状態 ${pick ? pick.status : 'NO_BLOCK'}）`; break;
    case 'kept': s = `${targetLabel(a)}に形式外の返事が届きました。書式どおりの結果があるため切り替えていません（記録は残しました）`; break;
    case 'rejected': s = `${targetLabel(a)}には採用できません（書式どおりの統合・完了報告が必要、または依頼前）。記録は残しました`; break;
    case 'late': s = `古い依頼（${a.lateTask}）への返事です。記録だけ残し、採用していません`; break;
    case 'stale': s = '古くなった実行タスクへの完了報告です。記録だけ残し、採用していません'; break;
    case 'unassigned': s = '誰宛ての返事か判定できません。下の「未割り当ての受信」で宛先を選んでください'; break;
    case 'failed': s = `解析に失敗しました（${fails.join('、')}）。今までの結果はそのまま使われます。AIに書式どおり再送を頼んでください` + (a.assignment ? '' : '（宛先は下で選べます）'); break;
    case 'noblock': s = '書式のブロックがありません（形式外）。下の「未割り当ての受信」で宛先を選んでください'; break;
    default: s = a.outcome;
  }
  if (a.notAdopted.length) s += `／同じ貼り付けの中の ${a.notAdopted.map(x => TAG_LABEL[x.tag]).join('・')} は採用していません（優先順で後）`;
  return s;
}
async function receiveText(c, text, via, expectedTask) {
  let h = null; try { h = await sha256Hex(new TextEncoder().encode(text)); } catch (_) { }
  const a = StudioState.receive(c, text, { via, now: now(), rawSha256: h, expectedTask: expectedTask || null });
  if (a.assignment && a.assignment.kind === 'RESULT' && a.outcome === 'adopted') updateAccess(c, a.assignment.name);
  log(c, `受信 ${a.attemptId}（${via}）: ${describeAttempt(a)}`);
  return a;
}
function assignAttempt(c, attemptId, value) {
  if (!value) return tell('割り当てられる宛先がありません');
  const [kind, name] = value.split('|');
  const r = StudioState.assign(c, attemptId, { kind, name: name || null }, now(), 'screen');
  if (!r.ok) { tell(r.why); return; }
  const a = StudioState.attempts(c).find(x => x.attemptId === attemptId);
  if (kind === 'RESULT' && r.outcome === 'adopted') updateAccess(c, name);
  log(c, `受信 ${attemptId} を ${targetLabel(a)} に割り当て（${r.outcome}）`);
  save(); render();
}
function setConfirm(c, attemptId, item, action) {
  StudioState.setConfirmation(c, attemptId, item, action, now(), 'screen');
  log(c, `${action === 'confirm' ? '確認' : '確認を取り消し'}: ${item}（受信 ${attemptId}）`); bump(c, 'gate');
  save(); render();
}

// ---------- dispatch ----------
// 1c: the request text is generated once, stored as a request record (Q-1), then delivered. Every delivery attempt is
// appended to that record (Q-2). A failed delivery is sent again with the same Task ID and text (Q-3), never regenerated.
const KIND = { observe: 'OBS', synth: 'SYN', next: 'NEXT' };
// Requests being delivered (by request ID) and first observation requests being prepared (by Case and Task ID).
// A second click while one is in flight does nothing, so the same request is never sent twice in parallel (Codex C43).
const inFlight = new Set();
const isSending = req => !!req && inFlight.has(req.requestId);
const obsKey = (c, n) => 'obs:' + c.id + ':' + c.tasks[n].taskId;
const obsSending = (c, n) => inFlight.has(obsKey(c, n)) || isSending(StudioState.currentRequest(c, 'OBS', n));
async function send(c, name, kind) {
  const S = StudioState;
  if (kind === 'observe' && !c.tasks[name].sentAt && S.canRetry(c, 'OBS', name)) return retrySend(c, 'OBS', name);   // first send failed: same request again
  const key = kind === 'observe' && !c.tasks[name].sentAt ? obsKey(c, name) : null;
  if (key) { if (inFlight.has(key)) return; inFlight.add(key); }
  try { return await prepareAndDeliver(c, name, kind); } finally { if (key) { inFlight.delete(key); render(); } }
}
async function prepareAndDeliver(c, name, kind) {
  const S = StudioState;
  let packet, taskId, gen;
  if (kind === 'observe') {
    if (c.tasks[name].sentAt) S.startObservationResend(c, name);          // re-send = new generation (G-1)
    taskId = c.tasks[name].taskId; gen = S.obsGen(c, name); packet = observePacket(c, name);
  } else if (kind === 'synth') {
    const reviseReason = c.gate && c.gate.decision === 'REVISE' && !S.gateStale(c) ? (c.gate.reason || '') : null;
    const r = S.startSynthesisRequest(c, name, now()); if (!r.ok) { tell(r.why); render(); return; }
    taskId = r.task; gen = c.synthesisReq.gen; packet = synthesisPacket(c, name, reviseReason);
  } else {
    const r = S.startNext(c, name, '', now()); if (!r.ok) { tell(r.why); render(); return; }   // guard inside the action itself (S-3)
    taskId = r.task; gen = c.next.gen; packet = nextPacket(c, name, taskId); c.next.packet = packet;
  }
  let sha = null; try { sha = await sha256Hex(new TextEncoder().encode(packet)); } catch (_) { }
  const req = S.recordRequest(c, { kind: KIND[kind], name, task: taskId, gen, packet, sha256: sha, at: now() });
  return deliver(c, req, false);                                           // deliver() stores the record first (Q-1)
}
async function retrySend(c, kind, name) {
  const req = StudioState.currentRequest(c, kind, name);
  if (!req || StudioState.deliveryState(req) !== 'failed') return tell('送り直せる依頼がありません（送付に失敗した今の依頼だけを送り直せます）');
  return deliver(c, req, true);
}
async function deliver(c, req, retry) {
  if (inFlight.has(req.requestId)) return;                                 // already being sent (double click)
  inFlight.add(req.requestId);
  try { render(); return await deliverOnce(c, req, retry); } finally { inFlight.delete(req.requestId); render(); }   // the lock is released on every path (Codex C44)
}
async function deliverOnce(c, req, retry) {
  const name = req.name, os = obsSetting(name), conn = Connectors[os.connector] || Connectors.manual;
  const what = req.kind === 'OBS' ? '観測' : req.kind === 'SYN' ? '統合' : '実行';
  // Q-1: nothing leaves the Studio unless its request text is stored. If the store cannot be written, stop here and
  // record the attempt as failed, so the same request can be sent again once saving works (Codex C42).
  if (!save()) {
    StudioState.recordDelivery(c, req.requestId, { at: now(), status: 'failed', connector: os.connector, error: '依頼文を保存できないため送付しませんでした', retry });
    log(c, `${name} への${what}の依頼 ${req.task} を送りませんでした（依頼文をこのブラウザに保存できないため）`);
    tell('依頼文をこのブラウザに保存できないため、送付を止めました（保存してから送る決まりです）。画面上部の警告を確認し、「記録をJSONで保存」で記録を退避してください。保存できる状態に戻ったら「同じ依頼を送り直す」で送れます。');
    render(); return;
  }
  try {
    const res = await conn.send({ caseId: c.id, taskId: req.task, observer: os, packet: req.packet, proxyUrl: store.settings.proxyUrl });
    StudioState.recordDelivery(c, req.requestId, { at: now(), status: res.status, connector: os.connector, retry,
      route: os.connector === 'proxy' ? (os.route || 'agent') : null, toFile: res.toFile || null, fromFile: res.fromFile || null });
    if (os.connector === 'manual') bump(c, 'copy'); else bump(c, 'autoSend');
    const stamp = { status: res.status, connector: os.connector };
    if (req.kind === 'OBS' && c.tasks[name] && c.tasks[name].taskId === req.task) { c.tasks[name].sentAt = now(); c.tasks[name].sendStatus = { at: now(), ...stamp }; }   // only the current task (Codex C42)
    else if (req.kind === 'SYN' && c.synthesisReq && c.synthesisReq.task === req.task) Object.assign(c.synthesisReq, stamp);
    else if (req.kind === 'NEXT' && c.next && c.next.task === req.task) Object.assign(c.next, stamp);
    log(c, `${name} へ${what}の依頼${retry ? 'を送り直し' : ''} ${req.task}（${os.connector === 'manual' ? '手動コピー' : 'ローカル中継'}・${res.status}）`);
    if (os.connector === 'manual') showPacket(req.packet, res.note, name, res.status === 'copied');
    if (res.status === 'answered' && res.text) { await receiveText(c, res.text, 'API', req.task); bump(c, 'autoReceive'); }
  } catch (e) {
    StudioState.recordDelivery(c, req.requestId, { at: now(), status: 'failed', connector: os.connector, error: e.message, retry });
    log(c, `${name} への${what}の依頼 ${req.task} の送付に失敗（${e.message}）。依頼文は保存済みで、同じ依頼を送り直せます`);
    tell(name + ' へ送れませんでした: ' + e.message + '\n依頼文は保存されています。原因を直してから「同じ依頼を送り直す」を押してください。');
  }
  save(); render(); schedulePoll();
}
// Show the stored request text (never regenerated). Copying it again is recorded in the log only; nothing else changes.
function showRequest(c, kind, name) {
  const S = StudioState, req = S.currentRequest(c, kind, name);
  const legacy = !req && kind === 'NEXT' && c.next && c.next.packet ? c.next.packet : null;
  const text = req ? req.packet : legacy;
  const task = req ? req.task : kind === 'OBS' ? c.tasks[name].taskId : kind === 'SYN' ? (c.synthesisReq && (c.synthesisReq.task || S.synTaskId(c))) : (c.next && (c.next.task || S.nextTaskId(c)));
  const st = req ? S.deliveryState(req) : null;
  const stLabel = { delivered: '送付済み', failed: '送付に失敗（同じ依頼を送り直せます）', unsent: '未送付' }[st] || '';
  const hist = req ? req.deliveries.map(d => `${hm(d.at)} ${d.retry ? '送り直し ' : ''}${d.status}${d.error ? '（' + d.error + '）' : ''}`).join(' → ') : '';
  const d = document.createElement('dialog');
  d.innerHTML = `<h2>依頼文（${esc(kind === 'OBS' ? name + ' への観測' : kind === 'SYN' ? '統合' : '実行タスク')}）</h2>
    <p class="hint">Task ID: ${esc(task || '—')}${req ? `・世代 ${req.gen}・作成 ${esc(hm(req.at))}・SHA-256 ${esc((req.sha256 || '').slice(0, 16))}…・${esc(stLabel)}` : ''}${hist ? '<br>送付: ' + esc(hist) : ''}</p>
    ${text ? `${legacy ? '<p class="hint">旧形式で保存された本文です（ハッシュなし）。</p>' : '<p class="hint">保存済みの本文そのものです。コピーしても新しい依頼にはなりません。</p>'}<textarea rows="14" readonly>${esc(text)}</textarea>`
      : '<p class="warn">この依頼の本文は保存されていません（この機能より前に作られた依頼です）。「作り直して再送」は新しい世代の別の依頼になり、元の依頼文の代わりにはなりません。</p>'}
    <div class="row end">${text ? '<button class="btn primary" value="copy">本文をコピー</button>' : ''}<button class="btn" value="close">閉じる</button></div>`;
  document.body.appendChild(d); d.showModal();
  d.querySelectorAll('button').forEach(b => b.onclick = async () => {
    if (b.value === 'copy') {
      let ok = false; try { await navigator.clipboard.writeText(text); ok = true; } catch (_) { const ta = d.querySelector('textarea'); ta.select(); try { ok = document.execCommand('copy'); } catch (__) { } }
      b.textContent = ok ? 'コピーしました' : 'コピーできません。上の本文を手で選んでコピーしてください';
      if (ok) { bump(c, 'copy'); log(c, `依頼文を再コピー ${task}（保存済みの本文${req && req.sha256 ? '・SHA-256 ' + req.sha256.slice(0, 8) + '…' : ''}。新しい依頼ではありません）`); save(); render(); }
      return;
    }
    d.close(); d.remove();
  });
  d.addEventListener('cancel', () => d.remove());
}
// 1d-3 / 1d-B: on the relay's file route (agent) the relay does not start the AI. A person tells the AI which file to read and
// which file to write. The guide follows the delivery record, not today's settings (Codex C51): it is shown only when the
// current request was delivered on the agent route, and it uses the files the relay reported it wrote. Only a relay that does
// not report them falls back to an estimate (marked as such), built from the exchange folder confirmed for the same relay URL.
function agentTarget(c, kind, name) {
  const req = StudioState.currentRequest(c, kind, name);
  if (!req) return null;
  const d = [...req.deliveries].reverse().find(x => x.status === 'queued');
  if (!d || d.route !== 'agent') return null;
  if (d.toFile && d.fromFile) return { toFile: d.toFile, fromFile: d.fromFile, estimated: false, relative: false };
  const dir = store.settings.exchangeFor && store.settings.exchangeFor === store.settings.proxyUrl ? store.settings.exchangeDir : '';
  const p = StudioText.relayPaths(dir, c.id, req.task, req.name || name);
  return p ? { ...p, estimated: true } : null;
}
function showAgentGuide(c, kind, name) {
  const p = agentTarget(c, kind, name), text = StudioText.agentInstruction(p);
  const d = document.createElement('dialog');
  d.innerHTML = `<h2>${esc(name)} への指示（ファイル受け渡し）</h2>
    <p class="hint">中継は AI を起動しません。次の文面を ${esc(name)} に渡してください。${p && p.estimated ? '<br><b>このパスは推定です</b>（中継がファイルの場所を返さなかったため、Studio が計算しました。まれな文字を含む名前では、中継の実際の場所と違うことがあります）。' + (p.relative ? '受け渡しフォルダの場所も分からないので、中継のフォルダからの相対パスです。' : '') : p ? '中継が実際に書いたファイルの場所です。' : ''}</p>
    ${text ? `<textarea rows="10" readonly>${esc(text)}</textarea>` : '<p class="warn">AI の名前または Task ID がファイル名に使えないため、パスを作れません。</p>'}
    <div class="row end">${text ? '<button class="btn primary" value="copy">指示をコピー</button>' : ''}<button class="btn" value="close">閉じる</button></div>`;
  document.body.appendChild(d); d.showModal();
  d.querySelectorAll('button').forEach(b => b.onclick = async () => {
    if (b.value === 'copy') {
      let ok = false; try { await navigator.clipboard.writeText(text); ok = true; } catch (_) { const ta = d.querySelector('textarea'); ta.select(); try { ok = document.execCommand('copy'); } catch (__) { } }
      b.textContent = ok ? 'コピーしました' : 'コピーできません。上の文面を手で選んでコピーしてください';
      if (ok) { bump(c, 'copy'); log(c, `${name} への指示（ファイル受け渡し）をコピー：${p.toFile}`); save(); render(); }
      return;
    }
    d.close(); d.remove();
  });
  d.addEventListener('cancel', () => d.remove());
}
async function autoDispatch(c) {
  for (const n of c.observers) if (obsSetting(n).connector !== 'manual' && !c.tasks[n].sentAt) await send(c, n, 'observe');
}
// G-5: collect only the Task IDs of the current generations; a task is collected until one receipt for it exists.
// Collections run one at a time (the 8 s timer and the manual button can overlap): each run sees the attempts
// recorded by the previous one, so the same relay text is never recorded twice (Codex C40 a).
let collectQueue = Promise.resolve(0);
function collectAll(c, quiet) { const run = collectQueue.then(() => collectOnce(c, quiet)); collectQueue = run.catch(() => 0); return run; }
async function collectOnce(c, quiet) {
  if (!store.settings.proxyUrl) return 0; let got = 0;
  // Keep polling a task until a structured reply is adopted for it (A-01 over the relay: a failed, late or informal
  // (NO_BLOCK) reply must not stop a formatted reply from being collected later; decided 10/13, Codex C40 c).
  // picked is set only when a tagged block was adopted. The same text is never recorded twice.
  const has = task => StudioState.attempts(c).some(a => a.picked && a.assignment && a.assignment.task === task && (a.outcome === 'adopted' || a.assignmentOutcome === 'adopted'));
  const seen = (task, text) => StudioState.attempts(c).some(a => a.expectedTask === task && a.raw === text);
  const jobs = [];
  for (const n of c.observers) { const t = c.tasks[n]; if (t.sentAt && obsSetting(n).connector === 'proxy' && !has(t.taskId) && !(t.result && !t.result.attemptId)) jobs.push([n, t.taskId]); }
  if (c.synthesisReq && obsSetting(c.synthesisReq.to).connector === 'proxy') { const t = c.synthesisReq.task || StudioState.synTaskId(c); if (!has(t) && !(c.synthesis && !c.synthesisReq.task)) jobs.push([c.synthesisReq.to, t]); }
  if (c.next && !StudioState.nextStale(c) && obsSetting(c.next.to).connector === 'proxy') { const t = c.next.task || StudioState.nextTaskId(c); if (!has(t) && !(c.done && !c.next.task)) jobs.push([c.next.to, t]); }
  for (const [n, taskId] of jobs) {
    try { const text = await Connectors.proxy.collect({ caseId: c.id, taskId, observer: obsSetting(n), proxyUrl: store.settings.proxyUrl });
      if (text && !seen(taskId, text)) { await receiveText(c, text, 'ローカル中継', taskId); bump(c, 'autoReceive'); got++; } }
    catch (e) { if (!quiet) log(c, `${n} の回収に失敗（${e.message}）`); }
  }
  if (got) { save(); render(); }
  return got;
}
function schedulePoll() {
  clearInterval(pollTimer); const c = cur(); if (!c || !store.settings.proxyUrl) return;
  pollTimer = setInterval(() => { const c2 = cur(); if (c2) collectAll(c2, true); }, 8000);
}
// After a manual send, say in plain steps what the human does next (the packet is shown only if the clipboard failed).
function showPacket(packet, note, name, copied) {
  const steps = name ? `<ol class="steps"><li>${copied ? '依頼文をコピーしました' : '下の依頼文を全部選んでコピー'}</li><li><b>${esc(name)}</b> を開き、貼り付けて送る</li><li>${esc(name)} の返事を<b>全部</b>コピー</li><li>この画面の「結果を受け取る」に貼って「取り込む」</li></ol>` : '';
  const d = document.createElement('dialog'); d.innerHTML = `<h2>${name ? esc(name) + ' へ依頼する手順' : '依頼文'}</h2>${steps}${note ? `<p class="hint">${esc(note)}</p>` : ''}${copied ? '' : `<textarea rows="12" readonly>${esc(packet)}</textarea>`}<div class="row end"><button class="btn primary">わかった</button></div>`;
  document.body.appendChild(d); d.querySelector('button').onclick = () => { d.close(); d.remove(); }; d.showModal(); d.querySelector('textarea')?.select();
}

// ---------- stage state (1b: counts and steps use eligibility and the stale rules only) ----------
function stageState(c) {
  const S = StudioState, n = c.observers.length, sent = c.observers.filter(x => c.tasks[x].sentAt).length, got = S.eligibleObservers(c).length;
  const synOk = !!c.synthesis && !S.synthesisStale(c), gateOk = !!c.gate && !S.gateStale(c), nextOk = !!c.next && !S.nextStale(c);
  const s = { CASE: 'done', SOURCE: c.sources.every(x => x.verified) ? 'done' : 'now', OBSERVE: got === n ? 'done' : 'now', DIFFERENCE: got >= 2 ? 'done' : '', SYNTHESIS: synOk ? 'done' : '', GATE: gateOk ? 'done' : '', NEXT: nextOk ? (S.canCountDone(c) ? 'done' : 'now') : '' };
  if (!c.sources.length) s.SOURCE = 'done';
  if (got === n || (c.synthesisReq && !synOk)) s.SYNTHESIS = synOk ? 'done' : 'now';
  if (synOk && !gateOk) s.GATE = 'now';
  if (gateOk && c.gate.decision === 'REVISE') { s.GATE = 'done'; s.SYNTHESIS = 'now'; }
  return { s, n, sent, got };
}
function nextStep(c) {
  const S = StudioState, { n, sent, got } = stageState(c); const acts = [];
  const pend = c.observers.filter(x => !c.tasks[x].sentAt);
  if (pend.length) {
    const unv = c.sources.filter(s => s.kind === 'ファイル' && !s.verified);
    if (unv.length) acts.push(['手元のファイルを確かめる', () => $('srcFile').click(), true]);
    for (const x of pend) acts.push(obsSending(c, x) ? [`${x}：送付中…`, () => { }] : [S.canRetry(c, 'OBS', x) ? `${x} へ同じ依頼を送り直す` : obsSetting(x).connector === 'manual' ? `${x} への依頼をコピー` : `${x} へ送る`, () => send(c, x, 'observe'), !unv.length]);
    const pre = unv.length ? `資料 ${unv.map(s => s.id).join('・')}（手元のファイル）はまだ確かめていません。先に「手元のファイルを確かめる」で選ぶと、確認済みとして依頼文に載ります。` : '';
    return [pre + `各AIへ観測を依頼します（${sent}/${n} 送付済み）。手動のAIは、コピーした依頼文をそのAIに貼ってください。`, acts];
  }
  const stale = !!c.synthesisReq && S.synthesisStale(c);
  const revised = !!c.gate && c.gate.decision === 'REVISE' && !S.gateStale(c);
  if (!c.synthesisReq || stale || revised) {
    if (store.settings.proxyUrl) acts.push(['中継から結果を回収', () => collectAll(c)]);
    if (!S.canRequestSynthesis(c)) return [`使える観測がまだありません（利用可能 0/${n}）。届いた回答は「結果を受け取る」に貼り、確認が必要な項目は「観測」欄で確認してください。`, acts];
    const pre = stale ? '統合の依頼後に観測が変わったため、今の統合・判断・実行タスクは古くなりました（記録は残っています）。' : revised ? '差し戻し（REVISE）されました。' : '';
    if (got < n) acts.push([stale || revised ? '今の観測で統合をやり直す' : '届いた分で統合へ進む', async () => { if (await ask(`${n - got} 件が未着または未確認です。使える ${got} 件で統合を依頼しますか？（未着・未確認は統合依頼に明記されます）`)) pickAndSend(c, 'synth'); }, stale || revised]);
    else acts.push([stale || revised ? '統合をやり直す' : '統合を依頼する', () => pickAndSend(c, 'synth'), true]);
    return [pre + (got < n ? `使える観測 ${got}/${n} 件。` : '全員の観測がそろいました。') + '統合をどのAIに頼むか選んで送ってください。', acts];
  }
  if (!c.synthesis && S.canRetry(c, 'SYN')) { acts.push(isSending(S.currentRequest(c, 'SYN')) ? ['送付中…', () => { }] : ['同じ依頼を送り直す', () => retrySend(c, 'SYN'), true], ['依頼文を表示・コピー', () => showRequest(c, 'SYN')]); return [`${c.synthesisReq.to} への統合の依頼（${c.synthesisReq.task || StudioState.synTaskId(c)}）は送付に失敗しました。依頼文は保存済みです。原因（中継の停止など）を直してから、同じ依頼を送り直してください。`, acts]; }
  if (!c.synthesis) { acts.push(['依頼文を表示・コピー', () => showRequest(c, 'SYN')]); if (agentTarget(c, 'SYN', c.synthesisReq.to)) acts.push(['AIへの指示を表示', () => showAgentGuide(c, 'SYN', c.synthesisReq.to)]); if (store.settings.proxyUrl) acts.push(['中継から統合を回収', () => collectAll(c)]); return [`${c.synthesisReq.to} の統合結果を待っています（${c.synthesisReq.task || StudioState.synTaskId(c)}）。届いたら「結果を受け取る」に貼ってください。`, acts]; }
  if (!S.synthesisEligible(c)) return ['統合結果に確認が必要な項目があります。「統合」欄で確認してから判断してください。', acts];
  if (!c.gate || S.gateStale(c)) return ['統合結果が届きました。右の「人間の判断」で APPROVE / HOLD / REVISE を選んでください。', acts];
  if (c.gate.decision === 'HOLD') return ['保留中です。確かめることが分かったら、新しい問いを始めるか、判断をやり直してください。', acts];
  if (!c.next || S.nextStale(c)) {
    if (S.canSendNext(c)) acts.push(['実行タスクを送る', () => pickAndSend(c, 'next'), true]);
    return [(c.next ? '前の実行タスクは古くなりました。' : '') + '承認されました。実行タスクを実装AIへ送ってください。', acts];
  }
  if (!c.done && S.canRetry(c, 'NEXT')) { acts.push(isSending(S.currentRequest(c, 'NEXT')) ? ['送付中…', () => { }] : ['同じ依頼を送り直す', () => retrySend(c, 'NEXT'), true], ['依頼文を表示・コピー', () => showRequest(c, 'NEXT')]); return [`${c.next.to} への実行タスク（${c.next.task || StudioState.nextTaskId(c)}）は送付に失敗しました。依頼文は保存済みです。原因を直してから、同じ依頼を送り直してください。`, acts]; }
  if (!S.canCountDone(c)) {
    if (!c.done) acts.push(['依頼文を表示・コピー', () => showRequest(c, 'NEXT')]);
    if (!c.done && agentTarget(c, 'NEXT', c.next.to)) acts.push(['AIへの指示を表示', () => showAgentGuide(c, 'NEXT', c.next.to)]);
    if (store.settings.proxyUrl) acts.push(['中継から実行報告を回収', () => collectAll(c)]);
    return [c.done ? '完了報告に確認が必要な項目があるか、古い依頼への報告です。「次の行動」欄を確認してください。' : `${c.next.to} に実行タスクを渡しました（${c.next.task || StudioState.nextTaskId(c)}）。完了報告が届いたら貼ってください。`, acts];
  }
  return ['このCaseは一周しました。記録を保存するか、次の問いを始めてください。', acts];
}
function pickAndSend(c, kind) {
  const def = kind === 'synth' ? (c.observers.includes('GPT') ? 'GPT' : c.observers[0]) : (c.observers.includes('Claude Code') ? 'Claude Code' : c.observers[0]);
  const d = document.createElement('dialog');
  d.innerHTML = `<h2>${kind === 'synth' ? '統合を頼むAI' : '実行を頼むAI'}</h2><select>${store.settings.observers.map(o => `<option${o.name === def ? ' selected' : ''}>${esc(o.name)}</option>`).join('')}</select><div class="row end"><button class="btn" value="cancel">やめる</button><button class="btn primary" value="ok">送る</button></div>`;
  document.body.appendChild(d); d.showModal();
  d.querySelectorAll('button').forEach(b => b.onclick = () => { const v = b.value, name = d.querySelector('select').value; d.close(); d.remove(); if (v === 'ok') send(c, name, kind); });
}

// ---------- render ----------
function render() {
  const c = cur();
  $('start').hidden = !!c; $('work').hidden = !c;
  $('caseList').innerHTML = store.order.map(id => { const x = store.cases[id]; if (!x) return ''; return `<button class="case-item${id === store.currentId ? ' sel' : ''}" data-id="${id}">${esc(oneLine(x.question).slice(0, 40))}<small>${id}</small></button>`; }).join('') || '<div class="hint">まだありません</div>';
  $('netNote').textContent = store.settings.proxyUrl ? 'ローカル中継のみ（' + store.settings.proxyUrl + '）' : 'なし';
  if (!c) { $('obsPick').innerHTML = store.settings.observers.map(o => `<label><input type="checkbox" value="${esc(o.name)}" checked> ${esc(o.name)}</label>`).join(''); return; }
  $('caseId').textContent = `${c.id}　作成 ${hm(c.createdAt)}`; $('caseTitle').textContent = c.question.split('\n')[0];
  const st = stageState(c);
  $('stepper').innerHTML = STAGES.map(([k, j], i) => `<li class="${st.s[k]}"><b>${String(i + 1).padStart(2, '0')} ${j}</b>${k}</li>`).join('');
  const [txt, acts] = nextStep(c);
  $('nextText').textContent = txt; const na = $('nextActions'); na.innerHTML = '';
  for (const [label, fn, primary] of acts) { const b = document.createElement('button'); b.className = 'btn' + (primary ? ' primary' : ''); b.textContent = label; b.onclick = fn; na.appendChild(b); }
  renderSources(c); renderObservers(c); renderDifference(c); renderSynthesis(c); renderGate(c); renderNext(c); renderMeter(c);
  $('log').innerHTML = c.log.slice().reverse().map(l => `<li>${hm(l.at)} — ${esc(l.t)}</li>`).join('');
}
function renderSources(c) {
  $('sources').innerHTML = c.sources.map(s => { const [t, k] = srcState(s);
    const acc = Object.entries(s.access).map(([n, a]) => `<span class="chip ${a.status === 'DIRECT' || a.status === 'READABLE' ? 'ok' : a.status === 'INACCESSIBLE' ? 'ng' : ''}" title="${esc(a.note)}">${esc(n)}: ${a.status}</span>`).join(' ');
    return `<div class="item"><div class="item-h"><b>${s.id}</b><span class="chip info">${esc(s.kind)}</span><span class="chip ${k}">${t}</span></div>
      <div class="ref">${esc(s.ref)}</div>${s.verified ? `<div class="ref">SHA-256 ${s.verified.sha256}（${s.verified.size} bytes／${esc(s.verified.by)}）</div>` : s.verifyNote ? `<div class="hint">${esc(s.verifyNote)}</div>` : ''}
      ${s.claimedHash ? `<div class="ref">申告 ${s.claimedHash}</div>` : ''}<div class="hint">AIが読めたか：${acc || 'まだ報告なし'}</div></div>`; }).join('') || '<div class="hint">問いの本文だけを対象にします。資料があれば下で追加できます。</div>';
}
const STATUS_LABEL = { OK: '完全', PARTIAL: '一部', UNCONFIRMED: '未確認（閉じタグなし）', NO_BLOCK: '形式外', PARSE_FAILED: '解析失敗' };
const statusLabel = r => !r ? '' : r.status ? STATUS_LABEL[r.status] || r.status : '旧形式（' + (r.format || '') + '）';
// Confirm / revoke buttons for one adopted record (K-1..K-3). Items still needing a decision come first.
function confirmControls(c, kind, rec, name) {
  if (!rec || !rec.status) return '';
  const id = StudioState.resultAttemptId(c, kind, rec, name);
  const pending = StudioState.pendingConfirmations(c, kind, rec, name);
  const done = StudioState.confirmedItems(c, id);
  const label = it => it === 'UNCLOSED' ? '閉じタグなし（途中で切れていないか）' : it === 'CLASSIFICATION_UNCERTAIN' ? '項目名を認識できない行がある（値の分類が未確定）' : it.startsWith('TEMPLATE_VALUE:') ? it.slice(15) + ' が依頼文の見本と同じ文言' : it.startsWith('DUPLICATE_KEY:') ? it.slice(14) + ' が2回あり値が違う（最初の値を使用）' : it;
  return (pending.length ? `<div class="warn">確認が必要：${pending.map(it => `<div>${esc(label(it))} <button class="btn" data-confirm="${esc(id)}" data-item="${esc(it)}">確認した</button></div>`).join('')}</div>` : '')
    + (done.length ? `<div class="hint">確認済み：${done.map(it => `${esc(label(it))} <button class="btn ghost" data-revoke="${esc(id)}" data-item="${esc(it)}">取り消す</button>`).join(' ')}</div>` : '');
}
function bindConfirm(c, root) {
  root.querySelectorAll('[data-confirm]').forEach(b => b.onclick = () => setConfirm(c, b.dataset.confirm, b.dataset.item, 'confirm'));
  root.querySelectorAll('[data-revoke]').forEach(b => b.onclick = () => setConfirm(c, b.dataset.revoke, b.dataset.item, 'revoke'));
}
function renderObservers(c) {
  const S = StudioState;
  $('observers').innerHTML = c.observers.map(n => { const t = c.tasks[n], os = obsSetting(n), r = t.result;
    const recv = S.receivedCount(c, n), ok = S.eligibleRecord(c, 'RESULT', r, n);
    const st = r ? `<span class="chip ${ok ? 'ok' : 'wait'}">採用中：${esc(statusLabel(r))}</span><span class="chip ${ok ? 'ok' : 'ng'}">${ok ? '利用可能' : '利用不可（確認待ち）'}</span>`
      : t.sentAt ? '<span class="chip wait">依頼済み・結果待ち</span>' : '<span class="chip">未依頼</span>';
    const gen = S.obsGen(c, n), req = S.currentRequest(c, 'OBS', n), failed = S.canRetry(c, 'OBS', n);
    return `<div class="item"><div class="item-h"><b>${esc(n)}</b>${st}<span class="chip">受信 ${recv}</span><span class="chip">${os.connector === 'manual' ? '手動' : 'ローカル中継'}</span>${gen > 1 ? `<span class="chip">依頼 第${gen}回</span>` : ''}
      ${failed ? '<span class="chip ng">送付に失敗</span>' : ''}
      ${req || t.sentAt ? `<button class="btn ghost" data-req="${esc(n)}">依頼文を表示</button>` : ''}
      ${t.sentAt && !r && !failed && agentTarget(c, 'OBS', n) ? `<button class="btn" data-guide="${esc(n)}">AIへの指示を表示</button>` : ''}
      ${obsSending(c, n) ? '<button class="btn" disabled>送付中…</button>' : `${failed ? `<button class="btn primary" data-retry="${esc(n)}">同じ依頼を送り直す</button>` : ''}
      ${failed && !t.sentAt ? '' : `<button class="btn" data-send="${esc(n)}">${t.sentAt ? '作り直して再送（新しい世代）' : os.connector === 'manual' ? '依頼文をコピー' : '送る'}</button>`}`}</div>
      ${r ? confirmControls(c, 'RESULT', r, n) : ''}
      ${r ? `<dl class="obs-body"><dt>観測</dt><dd>${esc(r.fields.OBSERVATION || '（ブロックなし。原文を保存）')}</dd><dt>根拠</dt><dd>${esc(r.fields.EVIDENCE || '—')}</dd><dt>不明</dt><dd>${esc(r.fields.UNKNOWN || '—')}</dd><dt>観測条件</dt><dd>${esc(r.fields.CONDITION || '—')}</dd></dl><details><summary class="hint">原文</summary><pre>${esc(r.raw)}</pre></details>` : ''}</div>`; }).join('');
  $('observers').querySelectorAll('[data-send]').forEach(b => b.onclick = () => send(c, b.dataset.send, 'observe'));
  $('observers').querySelectorAll('[data-req]').forEach(b => b.onclick = () => showRequest(c, 'OBS', b.dataset.req));
  $('observers').querySelectorAll('[data-guide]').forEach(b => b.onclick = () => showAgentGuide(c, 'OBS', b.dataset.guide));
  $('observers').querySelectorAll('[data-retry]').forEach(b => b.onclick = () => retrySend(c, 'OBS', b.dataset.retry));
  bindConfirm(c, $('observers'));
  renderUnassigned(c);
}
// R-6: receipts nobody could place. Assigning adds a record to the same receipt; it never confirms or approves anything.
function renderUnassigned(c) {
  const list = StudioState.unassigned(c);
  if (!list.length) { $('unassigned').innerHTML = ''; return; }
  // D12: a receipt whose block was picked by priority can only go to that block's kind (the same rule as StudioState.assign).
  const opts = a => (!a.picked || a.picked === 'RESULT' ? c.observers.map(n => `<option value="RESULT|${esc(n)}">${esc(n)} の観測</option>`).join('') : '')
    + ((!a.picked || a.picked === 'SYN') && c.synthesisReq ? '<option value="SYN|">統合</option>' : '') + ((!a.picked || a.picked === 'DONE') && c.next ? '<option value="DONE|">完了報告</option>' : '');
  $('unassigned').innerHTML = `<div class="warn"><b>未割り当ての受信（${list.length}）</b>${list.map(a => `<div class="item"><span class="hint">${hm(a.at)}・${esc(a.via)}・${esc(describeAttempt(a))}</span>
    <details><summary class="hint">原文</summary><pre>${esc(a.raw)}</pre></details>
    <div class="row"><select data-sel="${esc(a.attemptId)}">${opts(a) || '<option value="">（割り当てられる宛先がありません）</option>'}</select><button class="btn" data-assign="${esc(a.attemptId)}">この宛先に割り当てる</button></div></div>`).join('')}</div>`;
  $('unassigned').querySelectorAll('[data-assign]').forEach(b => b.onclick = () => assignAttempt(c, b.dataset.assign, $('unassigned').querySelector(`[data-sel="${b.dataset.assign}"]`).value));
}
function autoDifferences(c) {
  const out = [], names = StudioState.eligibleObservers(c);
  for (const s of c.sources) { const acc = Object.entries(s.access).filter(([n]) => names.includes(n)); const st = [...new Set(acc.map(([, a]) => a.status))];
    if (st.length > 1) out.push({ text: `${s.id} の読めた状態がAIによって違う（${acc.map(([n, a]) => n + '=' + a.status).join('、')}）`, origin: 'OBSERVER', by: 'Studio（自動検出）' }); }
  const fmt = names.filter(n => c.tasks[n].result.format === '形式外');
  if (fmt.length) out.push({ text: `${fmt.join('、')} の回答は形式外のため、項目ごとの比較ができない`, origin: 'STATE', by: 'Studio（自動検出）' });
  // 1d-2: display only. Items split inside one line (the reply lost its line breaks) are marked; the stored synthesis is unchanged.
  if (c.synthesis && !StudioState.synthesisStale(c) && c.synthesis.fields.DIFFERENCES) for (const d of StudioText.splitDifferences(c.synthesis.fields.DIFFERENCES))
    out.push({ text: d.text, origin: d.origin, by: c.synthesis.by + (d.auto ? '・自動分割（改行なし）' : '') });
  return out;
}
function renderDifference(c) {
  const done = StudioState.eligibleObservers(c);
  if (done.length < 2) { $('difference').innerHTML = '<div class="hint">使える結果が2件以上そろうと、ここに並びます（未確認の結果は並べません）。</div>'; return; }
  const diffs = autoDifferences(c);
  $('difference').innerHTML = `<table class="tbl"><tr><th></th>${done.map(n => `<th>${esc(n)}</th>`).join('')}</tr>
    ${['OBSERVATION', 'UNKNOWN', 'SOURCE_ACCESS'].map(k => `<tr><td>${k === 'OBSERVATION' ? '観測' : k === 'UNKNOWN' ? '不明' : '資料'}</td>${done.map(n => `<td>${esc(oneLine(c.tasks[n].result.fields[k]) || '—')}</td>`).join('')}</tr>`).join('')}</table>
    <div style="margin-top:8px">${diffs.map(d => `<div class="item"><span class="chip info">${esc(d.origin)}</span> ${esc(d.text)} <span class="hint">— ${esc(d.by)}</span></div>`).join('') || '<div class="hint">自動で検出できる違いはありません。内容の違いは統合で整理します。</div>'}</div>`;
}
function staleBanner(c, what) { const r = StudioState.staleReason(c, what); return r ? `<div class="warn">古い：${esc(r)}（記録は残っています。この内容では先へ進めません）</div>` : ''; }
function renderSynthesis(c) {
  if (!c.synthesis) {
    const failed = !!c.synthesisReq && StudioState.canRetry(c, 'SYN');
    $('synthesis').innerHTML = `<div class="hint">${c.synthesisReq ? esc(c.synthesisReq.to) + (failed ? ' への統合の依頼は送付に失敗しました（' : ' に統合を依頼済み（') + esc(c.synthesisReq.task || StudioState.synTaskId(c)) + '）。' + (failed ? '依頼文は保存済みです。' : '結果を待っています。') : '使える観測がそろうと、統合を依頼できます。'}</div>`
      + (c.synthesisReq ? `<div class="row"><button class="btn ghost" data-req-syn>依頼文を表示</button>${failed ? (isSending(StudioState.currentRequest(c, 'SYN')) ? '<button class="btn" disabled>送付中…</button>' : '<button class="btn primary" data-retry-syn>同じ依頼を送り直す</button>') : ''}</div>` : '');
    const bs = $('synthesis').querySelector('[data-req-syn]'); if (bs) bs.onclick = () => showRequest(c, 'SYN');
    const br = $('synthesis').querySelector('[data-retry-syn]'); if (br) br.onclick = () => retrySend(c, 'SYN');
    return; }
  const f = c.synthesis.fields; const L = [['共通点', 'COMMON'], ['相違点', 'DIFFERENCES'], ['不明', 'UNKNOWN'], ['保留', 'HOLD'], ['実行に必要なこと', 'EXECUTION_NEEDS'], ['次のTask', 'NEXT_TASK'], ['人間が決めること', 'GATE_QUESTION']];
  $('synthesis').innerHTML = `${staleBanner(c, 'SYN')}<div class="hint">統合: ${esc(c.synthesis.by)}（${hm(c.synthesis.at)}・${esc(statusLabel(c.synthesis))}）${c.synthesis.missing.length ? '・不足項目 ' + c.synthesis.missing.join(', ') : ''}</div>${confirmControls(c, 'SYN', c.synthesis)}<dl class="obs-body">${L.map(([j, k]) => `<dt>${j}</dt><dd>${esc(f[k] || '—')}</dd>`).join('')}</dl>`;
  bindConfirm(c, $('synthesis'));
}
function renderGate(c) {
  if (!c.synthesis && !c.gate) { $('gate').innerHTML = '<div class="hint">統合結果が届くと、ここで判断します。</div>'; return; }
  const S = StudioState, can = S.canGate(c), q = c.synthesis && c.synthesis.fields.GATE_QUESTION;
  $('gate').innerHTML = `${c.gate ? staleBanner(c, 'GATE') : ''}${q ? `<div class="warn">${esc(q)}</div>` : ''}
    <textarea id="gateReason" rows="3" placeholder="判断の理由（何を採用し、何を保留し、何を直すか）"${can ? '' : ' disabled'}>${esc(c.gate ? c.gate.reason : '')}</textarea>
    <div class="row"><button class="btn ok" data-g="APPROVE"${can ? '' : ' disabled'}>APPROVE 採用</button><button class="btn hold" data-g="HOLD"${can ? '' : ' disabled'}>HOLD 保留</button><button class="btn ng" data-g="REVISE"${can ? '' : ' disabled'}>REVISE 差し戻し</button>
    ${c.gate ? `<span class="chip ${S.gateStale(c) ? 'ng' : c.gate.decision === 'APPROVE' ? 'ok' : c.gate.decision === 'HOLD' ? 'wait' : 'ng'}">${S.gateStale(c) ? '古い判断' : '現在'}：${c.gate.decision}（${hm(c.gate.at)}）</span>` : '<span class="chip">未判断</span>'}</div>
    ${can ? '' : '<div class="hint">今は判断できません（統合が古い・未着・確認待ち）。</div>'}
    ${(c.gateHistory || []).length ? `<div class="hint">これまでの判断：${c.gateHistory.map(g => g.decision + '（' + hm(g.at) + '）').join(' → ')}</div>` : ''}`;
  $('gate').querySelectorAll('[data-g]').forEach(b => b.onclick = () => {
    const r = S.recordGate(c, b.dataset.g, $('gateReason').value.trim(), now());      // guard inside the action (S-3)
    if (!r.ok) return tell(r.why);
    bump(c, 'gate'); if (c.gate.reason) bump(c, 'typed');
    log(c, `人間の判断: ${c.gate.decision}${c.gate.reason ? '（' + oneLine(c.gate.reason) + '）' : ''}`); save(); render();
  });
}
function renderNext(c) {
  const S = StudioState;
  if (!c.next) { $('next').innerHTML = `<div class="hint">${S.canSendNext(c) ? '実行タスクを送れます（上の「次にすること」）。' : '人間が APPROVE すると、実装AIへの実行タスクが作られます。'}</div>`; return; }
  const d = c.done;
  $('next').innerHTML = `${staleBanner(c, 'NEXT')}<div class="hint">${esc(c.next.to)} への実行タスク（作成 ${hm(c.next.at)}・${esc(c.next.task || '')}）</div><details><summary class="hint">実行タスクの全文</summary><pre>${esc(c.next.packet)}</pre></details><div class="row"><button class="btn ghost" data-req-next>依頼文を表示・コピー</button>${S.canRetry(c, 'NEXT') ? '<span class="chip ng">送付に失敗</span>' + (isSending(S.currentRequest(c, 'NEXT')) ? '<button class="btn" disabled>送付中…</button>' : '<button class="btn primary" data-retry-next>同じ依頼を送り直す</button>') : ''}</div>
    ${d ? `${staleBanner(c, 'DONE')}${confirmControls(c, 'DONE', d)}<dl class="obs-body"><dt>完了報告（${esc(d.by || '')}・${esc(statusLabel(d))}${S.canCountDone(c) ? '' : '・完了に数えていません'}）</dt><dd>${esc(d.fields.CHANGED || '')}</dd><dt>確かめたこと</dt><dd>${esc(d.fields.VERIFIED || '—')}</dd><dt>不明</dt><dd>${esc(d.fields.UNKNOWN || '—')}</dd><dt>次に人間が決めること</dt><dd>${esc(d.fields.NEXT || '—')}</dd></dl>` : '<div class="hint">完了報告を待っています。</div>'}`;
  bindConfirm(c, $('next'));
  const bn = $('next').querySelector('[data-req-next]'); if (bn) bn.onclick = () => showRequest(c, 'NEXT');
  const bnr = $('next').querySelector('[data-retry-next]'); if (bnr) bnr.onclick = () => retrySend(c, 'NEXT');
}
function renderMeter(c) {
  const m = c.metrics, carry = (m.copy || 0) + (m.paste || 0) + (m.drop || 0);
  $('meter').innerHTML = `<div><b>${carry}</b><span>人間の運搬（コピー＋貼り付け＋ファイル）</span></div><div><b>${m.copy || 0}</b><span>コピー</span></div><div><b>${(m.paste || 0) + (m.drop || 0)}</b><span>貼り付け・ドロップ</span></div><div><b>${(m.autoSend || 0) + (m.autoReceive || 0)}</b><span>Studioが自動で運んだ回数</span></div><div><b>${(m.typed || 0) + (m.gate || 0)}</b><span>人間の入力・判断</span></div>`;
}

// ---------- settings ----------
// 1d-4: the route only matters for the relay; a stored value outside the list (Meta's 'manual') is shown as it is.
const ROUTES = ['agent', 'api:openai', 'api:anthropic', 'api:gemini', 'mock'];
function renderSettings() {
  $('proxyUrl').value = store.settings.proxyUrl || '';
  $('connRows').innerHTML = store.settings.observers.map((o, i) => `<tr><td>${esc(o.name)}</td><td><select data-i="${i}" data-k="connector"><option value="manual"${o.connector === 'manual' ? ' selected' : ''}>手動</option><option value="proxy"${o.connector === 'proxy' ? ' selected' : ''}>ローカル中継</option></select></td>
    <td><select data-i="${i}" data-k="route"${o.connector === 'manual' ? ' disabled title="手動のときは使いません"' : ''}>${ROUTES.includes(o.route) ? '' : `<option value="${esc(o.route || '')}" selected>${!o.route || o.route === 'manual' ? '（使わない：手動）' : esc(o.route) + '（今の設定）'}</option>`}${ROUTES.map(r => `<option value="${r}"${o.route === r ? ' selected' : ''}>${r === 'agent' ? 'ファイル受け渡し（Claude Code・Codex等）' : r === 'mock' ? 'テスト用の模擬応答' : 'API ' + r.slice(4)}</option>`).join('')}</select></td></tr>`).join('');
  $('connRows').querySelectorAll('select').forEach(s => s.onchange = () => { store.settings.observers[+s.dataset.i][s.dataset.k] = s.value; save(); if (s.dataset.k === 'connector') renderSettings(); });
}

// ---------- events ----------
function bind() {
  $('startBtn').onclick = () => { const q = $('qInput').value.trim(); if (!q) { $('qInput').focus(); return; }
    const names = [...$('obsPick').querySelectorAll('input:checked')].map(x => x.value); if (!names.length) return tell('AIを1つ以上選んでください');
    const premise = { title: $('premiseTitle').value, text: $('premiseInput').value };
    $('qInput').value = ''; $('premiseInput').value = ''; $('premiseTitle').value = ''; createCase(q, names, premise); };
  $('newCaseBtn').onclick = () => { store.currentId = null; save(); render(); $('qInput').focus(); };
  $('caseList').onclick = e => { const b = e.target.closest('[data-id]'); if (!b) return; store.currentId = b.dataset.id; save(); render(); schedulePoll(); };
  $('pasteBtn').onclick = () => doPaste($('pasteBox').value, '貼り付け');
  $('pasteBox').addEventListener('dragover', e => e.preventDefault());
  $('pasteBox').addEventListener('drop', async e => { e.preventDefault(); const f = e.dataTransfer.files[0]; if (f) doPaste(await f.text(), 'ファイル'); });
  $('srcAddBtn').onclick = () => { const c = cur(), v = $('srcAdd').value.trim(); if (!v) return;
    // Typed by a person into "add SOURCE": bare file names are registered too (1d-1).
    const found = StudioText.extractSources(v); const list = found.length ? found : [{ id: '', kind: 'テキスト', ref: v, claimedHash: null, verified: null, access: {} }];
    // Skip anything already registered, so the same file or URL never becomes two SOURCEs.
    const known = new Set(c.sources.map(x => x.ref.trim().toLowerCase())), skipped = [];
    for (const s of list) { if (known.has(s.ref.trim().toLowerCase())) { skipped.push(s.ref); continue; } known.add(s.ref.trim().toLowerCase());
      s.id = 'S' + (c.sources.length + 1); c.sources.push(s); log(c, `資料 ${s.id} を追加`); }
    if (skipped.length) tell('登録済みのため追加しませんでした：\n' + skipped.join('\n'));
    bump(c, 'typed'); $('srcAdd').value = ''; save(); render(); verifySources(c); };
  $('srcTextBtn').onclick = async () => { const c = cur(), t = $('srcText').value; if (!t.trim()) return;
    const s = await makeTextSource('S' + (c.sources.length + 1), $('srcTextTitle').value, t);
    if (s.error) return tell(s.error);
    if (StudioText.isDuplicateText(c.sources, s)) return tell('同じ本文がすでに登録されています');
    c.sources.push(s); log(c, `資料 ${s.id}（本文「${s.ref}」）を追加`); bump(c, 'paste');
    $('srcText').value = ''; $('srcTextTitle').value = ''; save(); render();
    if (Object.values(c.tasks).some(t => t.sentAt)) tell('送付済みの依頼には入っていません。必要なら「作り直して再送」で新しい依頼文にしてください。'); };
  $('srcFile').onchange = async e => { const c = cur(), files = [...e.target.files]; e.target.value = ''; if (!files.length) return;
    for (const f of files) {
      const h = await sha256Hex(await f.arrayBuffer());
      let hit = c.sources.filter(x => x.ref.endsWith(f.name));
      if (!hit.length && h) hit = c.sources.filter(x => x.verified && x.verified.sha256 === h);
      if (!hit.length) { const s = { id: 'S' + (c.sources.length + 1), kind: 'ファイル', ref: f.name, claimedHash: null, verified: null, access: {} }; c.sources.push(s); hit = [s]; }
      for (const s of hit) {
        if (h) { s.verified = { sha256: h, size: f.size, by: 'Studio（手元のファイル）', at: now() }; delete s.verifyNote; log(c, `${s.id} を手元のファイルで確認`); }
        else s.verifyNote = 'このブラウザでは計算できません（http://localhost で開いてください）';
      }
    }
    bump(c, 'drop'); save(); render(); };
  $('exportBtn').onclick = () => { const c = cur(); download(c.id + '.json', JSON.stringify({ format: 'ats-operational-case', version: 1, exportedAt: now(), case: c }, null, 2), 'application/json'); };
  $('mdBtn').onclick = () => { const c = cur(); download(c.id + '.md', caseMd(c), 'text/markdown'); };
  $('importFile').onchange = async e => { const f = e.target.files[0]; e.target.value = ''; if (!f) return;
    try { const o = JSON.parse(await f.text()); if (o.format !== 'ats-operational-case' || !o.case || !o.case.id) throw new Error('このStudioの記録JSONではありません');
      let c = o.case; if (store.cases[c.id] && !await ask(c.id + ' は既にあります。読み込んだ内容で置き換えますか？')) return;
      store.cases[c.id] = c; if (!store.order.includes(c.id)) store.order.unshift(c.id); store.currentId = c.id;
      if (saveBlocked && await ask('保存が止まっています。読み込んだ内容で保存を再開しますか？（読めなかった保存データは上書きされます）')) { saveBlocked = false; $('saveWarn').hidden = true; }
      log(c, '記録JSONから読み込み'); save(); render(); } catch (err) { tell('読み込めません: ' + err.message); } };
  $('settingsBtn').onclick = () => { renderSettings(); $('settings').showModal(); };
  $('restoreBtn').onclick = () => restoreFromCopy();
  // Save the relay URL as it is typed, so re-rendering the dialog (e.g. adding an AI) never drops it.
  $('proxyUrl').oninput = () => { const v = $('proxyUrl').value.trim(); if (v !== store.settings.proxyUrl) { store.settings.exchangeDir = ''; store.settings.exchangeFor = ''; } store.settings.proxyUrl = v; save(); };
  $('settings').addEventListener('close', () => { save(); render(); renderBackupNote(); schedulePoll(); });
  $('proxyTest').onclick = async () => { try { const url = $('proxyUrl').value.trim(); const j = await proxyHealth(url); store.settings.exchangeDir = j.exchange || ''; store.settings.exchangeFor = url; save(); $('proxyMsg').textContent = `中継に届きました（使えるAPI: ${j.providers.join(', ') || 'なし'}／受け渡しフォルダ: ${j.exchange}）。AI との往復ができるかは、まだ確かめていません。`; } catch (e) { store.settings.exchangeDir = ''; store.settings.exchangeFor = ''; save(); $('proxyMsg').textContent = '接続できません: ' + e.message; } };
  $('newObsBtn').onclick = () => { const v = $('newObs').value.trim(); if (!v || store.settings.observers.some(o => o.name === v)) return; store.settings.observers.push({ name: v, connector: 'manual', route: 'manual' }); $('newObs').value = ''; save(); renderSettings(); render(); };
}
async function doPaste(text, via) {
  const c = cur(); if (!text.trim()) return;
  const a = await receiveText(c, text, via);
  bump(c, via === 'ファイル' ? 'drop' : 'paste'); $('pasteBox').value = '';
  $('pasteMsg').textContent = describeAttempt(a); save(); render();
}
function download(name, text, type) { const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([text], { type })); a.download = name; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 1000); }
function caseMd(c) {
  const S = StudioState, L = [`# ${c.id}`, '', '## 問い', c.question, '', '## SOURCE'];
  for (const s of c.sources) L.push(`- ${s.id} ${s.kind} ${s.ref} — ${srcState(s)[0]}${s.verified ? ' sha256 ' + s.verified.sha256 : ''}｜読めたか: ${Object.entries(s.access).map(([n, a]) => n + '=' + a.status).join(', ') || '報告なし'}`);
  // The fence is longer than any run of backticks in the text, so a ``` inside a premise cannot close it early.
  for (const s of c.sources) if (s.kind === StudioText.TEXT_KIND) { const f = StudioText.fenceFor(s.text);
    L.push('', `### ${s.id} 本文「${s.ref}」`, f + 'text', s.text, f); }
  L.push('', '## OBSERVE');
  for (const n of c.observers) { const r = c.tasks[n].result;
    L.push(`### ${n}`, r ? `採用中 ${r.at}（${r.via}・${statusLabel(r)}・${S.eligibleRecord(c, 'RESULT', r, n) ? '利用可能' : '利用不可'}・受信 ${S.receivedCount(c, n)} 件）` : '未着', '');
    if (r) { const p = S.pendingConfirmations(c, 'RESULT', r, n); if (p.length) L.push('確認が必要: ' + p.join(', '), ''); L.push('```text', r.raw, '```', ''); } }
  L.push('## DIFFERENCE'); for (const d of autoDifferences(c)) L.push(`- [${d.origin}] ${d.text}（${d.by}）`);
  L.push('', '## SYNTHESIS', c.synthesis ? (S.synthesisStale(c) ? '（古い: ' + S.staleReason(c, 'SYN') + '）\n' : '') + '```text\n' + c.synthesis.raw + '\n```' : '（なし）', '', '## HUMAN GATE');
  for (const g of (c.gateHistory || [])) L.push(`- ${g.decision} ${g.at} ${g.reason || ''}`);
  if (c.gate && S.gateStale(c)) L.push('（今の判断は古い: ' + S.staleReason(c, 'GATE') + '）');
  L.push('', '## NEXT', c.next ? (S.nextStale(c) ? '（古い: ' + S.staleReason(c, 'NEXT') + '）\n' : '') + '```text\n' + c.next.packet + '\n```' : '（なし）', c.done ? '\n### 完了報告' + (S.canCountDone(c) ? '' : '（完了に数えていない）') + '\n```text\n' + c.done.raw + '\n```' : '', '', '## 受信の記録');
  for (const a of S.attempts(c)) L.push(`- ${a.at} ${a.attemptId} ${a.via} → ${describeAttempt(a)}`);
  L.push('', '## 依頼文（保存済みの本文）');
  if (!S.requests(c).length) L.push('（この Case には依頼文の記録がありません。この機能より前の依頼です）');
  for (const q of S.requests(c)) L.push('', `### ${q.kind} ${q.task}（${q.name || ''}・世代 ${q.gen}・${q.at}${q.sha256 ? '・SHA-256 ' + q.sha256 : ''}）`,
    '送付: ' + (q.deliveries.map(d => `${d.at} ${d.retry ? '送り直し ' : ''}${d.status}${d.error ? '（' + d.error + '）' : ''}`).join(' → ') || 'なし'), '```text', q.packet, '```');
  L.push('', '## 経過');
  for (const l of c.log) L.push(`- ${l.at} ${l.t}`);
  return L.join('\n');
}

load(); bind(); render(); renderBackupNote(); schedulePoll();
