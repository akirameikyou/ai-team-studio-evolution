'use strict';
// AI Team Studio — 実運用版 001
// Flow: question -> CASE -> SOURCE -> OBSERVE (independent) -> DIFFERENCE -> SYNTHESIS -> HUMAN GATE -> NEXT
// The human writes the question and makes the gate decision. Everything else is prepared by the Studio.

const KEY = 'ats-operational-001';
const STAGES = [['CASE', '問い'], ['SOURCE', '資料'], ['OBSERVE', '観測'], ['DIFFERENCE', '差分'], ['SYNTHESIS', '統合'], ['GATE', '判断'], ['NEXT', '次へ']];
const ACCESS = ['DIRECT', 'READABLE', 'INACCESSIBLE', 'UNKNOWN'];
const ORIGINS = ['SOURCE', 'CONTEXT', 'STATE', 'OBSERVER', 'EVIDENCE', 'REASONING', 'JUDGMENT'];
const RESULT_KEYS = ['CASE', 'TASK', 'FROM', 'SOURCE_ACCESS', 'CONDITION', 'OBSERVATION', 'EVIDENCE', 'UNKNOWN'];
const SYNTH_KEYS = ['CASE', 'TASK', 'FROM', 'COMMON', 'DIFFERENCES', 'UNKNOWN', 'HOLD', 'EXECUTION_NEEDS', 'NEXT_TASK', 'GATE_QUESTION'];
const DONE_KEYS = ['CASE', 'TASK', 'FROM', 'CHANGED', 'VERIFIED', 'UNKNOWN', 'NEXT'];
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
const hm = iso => iso ? iso.slice(5, 16).replace('T', ' ') : '';
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
function save() {
  if (saveBlocked) return;
  try { localStorage.setItem(KEY, JSON.stringify(store)); }
  catch (e) { flash('保存に失敗しました（' + e.name + '）。記録をJSONで保存してください。', true); }
}
function block(msg) { saveBlocked = true; setTimeout(() => flash(msg, true), 0); }
// Save problems get their own banner at the top of the page (render() never overwrites it).
function flash(msg, bad) { if (bad) { const w = $('saveWarn'); w.textContent = msg; w.hidden = false; } }
function log(c, t) { c.log.push({ at: now(), t }); }
function bump(c, k, n = 1) { c.metrics[k] = (c.metrics[k] || 0) + n; }

// ---------- case creation ----------
function newCaseId() {
  const d = new Date(), ymd = d.getFullYear() + String(d.getMonth() + 1).padStart(2, '0') + String(d.getDate()).padStart(2, '0');
  let n = 1; while (store.cases[`CASE-${ymd}-${String(n).padStart(3, '0')}`]) n++;
  return `CASE-${ymd}-${String(n).padStart(3, '0')}`;
}
function extractSources(text) {
  const out = []; const seen = new Set();
  const add = (kind, ref) => { if (seen.has(ref)) return; seen.add(ref); out.push({ id: 'S' + (out.length + 1), kind, ref, claimedHash: null, verified: null, access: {} }); };
  for (const m of text.matchAll(/https?:\/\/[^\s<>"'）)」]+/g)) add(/github(usercontent)?\.com/.test(m[0]) ? 'GitHub' : 'URL', m[0].replace(/[。、.,]+$/, ''));
  for (const m of text.matchAll(/[A-Za-z]:\\[^\s"'<>|]+/g)) add('ファイル', m[0]);
  for (const m of text.matchAll(/\b[\w.-]+\.(zip|pdf|md|json|html|csv|txt|docx|xlsx)\b/gi)) if (![...seen].some(r => r.includes(m[0]))) add('ファイル', m[0]);
  const hashes = [...text.matchAll(/\b[0-9a-f]{64}\b/gi)].map(m => m[0].toLowerCase());
  if (hashes.length && out.length) out[0].claimedHash = hashes[0];
  return out;
}
function createCase(question, observerNames) {
  const c = {
    id: newCaseId(), question: question.trim(), createdAt: now(), sources: extractSources(question),
    observers: observerNames, tasks: {}, synthesis: null, gate: null, gateHistory: [], next: null,
    metrics: { typed: 1 }, log: []
  };
  log(c, `問いを受け取り、CASEを作成（資料 ${c.sources.length}件を本文から取り出し）`);
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
function srcState(s) {
  if (s.verified && s.claimedHash) return s.verified.sha256 === s.claimedHash ? ['確認済み・申告と一致', 'ok'] : ['確認済み・申告と不一致', 'ng'];
  if (s.verified) return ['確認済み', 'ok'];
  if (s.claimedHash) return ['申告のみ（未確認）', 'wait'];
  return ['未確認', 'wait'];
}

// ---------- packets ----------
function sourceLines(c) {
  if (!c.sources.length) return ['- なし（問いの本文だけが対象）'];
  return c.sources.map(s => `- ${s.id}｜${s.kind}｜${s.ref}${s.verified ? `｜Studioで確認済み SHA-256 ${s.verified.sha256}（${s.verified.size} bytes）` : s.claimedHash ? `｜申告 SHA-256 ${s.claimedHash}（未確認）` : '｜未確認'}`);
}
function observePacket(c, name) {
  const t = c.tasks[name];
  return [
    `[AI TEAM STUDIO｜観測依頼]  CASE: ${c.id}  TASK: ${t.taskId}  TO: ${name}`, '',
    '■ 問い', c.question, '',
    '■ 資料（SOURCE）', ...sourceLines(c), '',
    '■ お願い',
    '- ほかのAIの結果は渡していません。あなた自身の観測として答えてください。',
    '- 資料を実際に読めたかどうかを、資料ごとに書いてください。読めなかった内容を推測で埋めないでください。',
    '- 根拠には DIRECT（自分で確かめた）／REPORTED（他から聞いた）／INFERENCE（推論）を付けてください。', '',
    '■ 最後に、次のブロックを1つだけ付けてください（項目名は英語のまま。翻訳しない）',
    '[STUDIO_RESULT]', `CASE: ${c.id}`, `TASK: ${t.taskId}`, `FROM: ${name}`,
    `SOURCE_ACCESS: ${c.sources.map(s => s.id + '=DIRECT|READABLE|INACCESSIBLE|UNKNOWN').join(', ') || 'N/A'}（理由）`,
    'CONDITION: モデル / 使えた道具 / ファイル可否 / Web可否 / 事前知識の有無',
    'OBSERVATION: 観測したこと・答え', 'EVIDENCE: 根拠（DIRECT/REPORTED/INFERENCE）', 'UNKNOWN: 確かめられなかったこと',
    '[/STUDIO_RESULT]'
  ].join('\n');
}
function synthesisPacket(c, name) {
  const done = c.observers.filter(n => c.tasks[n].result);
  const body = done.map(n => { const f = c.tasks[n].result.fields;
    return [`### ${n}`, `SOURCE_ACCESS: ${f.SOURCE_ACCESS || '（記載なし）'}`, `CONDITION: ${f.CONDITION || '（記載なし）'}`, `OBSERVATION: ${f.OBSERVATION || c.tasks[n].result.raw.slice(0, 1500)}`, `EVIDENCE: ${f.EVIDENCE || '（記載なし）'}`, `UNKNOWN: ${f.UNKNOWN || '（記載なし）'}`].join('\n'); });
  const missing = c.observers.filter(n => !c.tasks[n].result);
  const revise = c.gate && c.gate.decision === 'REVISE' ? ['', '■ 人間からの差し戻し理由', c.gate.reason || '（理由なし）'] : [];
  return [
    `[AI TEAM STUDIO｜統合依頼]  CASE: ${c.id}  TASK: ${c.id}-SYN  TO: ${name}`, '',
    '■ 問い', c.question, '', '■ 資料（SOURCE）', ...sourceLines(c), '',
    `■ 独立観測の結果（${done.length}件${missing.length ? '／未着: ' + missing.join(', ') : ''}）`, ...body, ...revise, '',
    '■ お願い',
    '- 要約や平均にしないでください。共通点・相違点・不明・保留を分け、相違点には生じた層の候補を付けてください。',
    `- 層の候補: ${ORIGINS.join(' / ')}`,
    '- 結論を確定しないでください。採否は人間が決めます。', '',
    '■ 最後に、次のブロックを1つだけ付けてください（項目名は英語のまま）',
    '[STUDIO_SYNTHESIS]', `CASE: ${c.id}`, `TASK: ${c.id}-SYN`, `FROM: ${name}`,
    'COMMON: 共通して言えること', 'DIFFERENCES: 1行に1つ「内容 | ORIGIN: 層」', 'UNKNOWN: まだ言えないこと', 'HOLD: 判断を保留すべきこと',
    'EXECUTION_NEEDS: 実行に必要な情報・条件', 'NEXT_TASK: 実装・作業AIへの具体的な依頼（作業単位を1つ）', 'GATE_QUESTION: 人間が決めるべきこと',
    '[/STUDIO_SYNTHESIS]'
  ].join('\n');
}
function nextPacket(c, name) {
  const s = c.synthesis.fields;
  return [
    `[AI TEAM STUDIO｜実行タスク]  CASE: ${c.id}  TASK: ${c.id}-NEXT  TO: ${name}`, '',
    '■ もとの問い', c.question, '',
    `■ 人間の判断: ${c.gate.decision}（${hm(c.gate.at)}）`, c.gate.reason || '（理由の記載なし）', '',
    '■ やってほしいこと（作業単位は1つ）', s.NEXT_TASK || '（統合結果にNEXT_TASKがありません）', '',
    '■ 実行に必要な情報・条件', s.EXECUTION_NEEDS || '（記載なし）', '',
    '■ 資料（SOURCE。状態つき。未確認のものを確認済みとして扱わない）', ...sourceLines(c), '',
    '■ 確定していないこと（推測で埋めない）', s.UNKNOWN || '（なし）', s.HOLD ? '保留: ' + s.HOLD : '', '',
    '■ 判断の経緯', `- 独立観測: ${c.observers.filter(n => c.tasks[n].result).join(', ')}`, `- 統合: ${c.synthesis.by}`, `- 共通点: ${oneLine(s.COMMON)}`, '',
    '■ 終わったら、次のブロックを付けて返してください',
    '[STUDIO_DONE]', `CASE: ${c.id}`, `TASK: ${c.id}-NEXT`, `FROM: ${name}`,
    'CHANGED: 変更したもの（ファイル・commit等）', 'VERIFIED: 実際に確かめたこと', 'UNKNOWN: 確かめられなかったこと', 'NEXT: 次に人間が決めること',
    '[/STUDIO_DONE]'
  ].filter(x => x !== '').join('\n');
}
const oneLine = s => String(s || '').replace(/\s+/g, ' ').trim().slice(0, 200);

// ---------- parsing (tolerant: full-width marks, joined lines, lowercase) ----------
function parseBlock(text, tag, keys) {
  const t = String(text || '').replace(/［/g, '[').replace(/］/g, ']').replace(/：/g, ':');
  const m = t.match(new RegExp('\\[' + tag + '\\]([\\s\\S]*?)(\\[\\/' + tag + '\\]|$)', 'i'));
  if (!m) return null;
  // Keys normally start a line. Only when an AI joined the lines (few line-start keys) fall back to splitting inside lines,
  // so a body that merely mentions "OBSERVATION:" is not cut. The first occurrence of a key wins.
  const scan = lead => { const re = new RegExp(lead + '(' + keys.join('|') + ')\\s*:', 'gi'), hits = []; let h;
    while ((h = re.exec(m[1]))) hits.push({ k: h[1].toUpperCase(), s: h.index + h[0].length, a: h.index }); return hits; };
  let hits = scan('(?:^|\\n)[ \\t]*'); if (hits.length < 3) hits = scan('(?:^|\\s)');
  const f = {}; hits.forEach((x, i) => { if (!(x.k in f)) f[x.k] = m[1].slice(x.s, i + 1 < hits.length ? hits[i + 1].a : m[1].length).trim(); });
  return { fields: f, missing: keys.filter(k => !(k in f)) };
}
function parseAccess(v) {
  const out = {}; for (const m of String(v || '').matchAll(/(S\d+)\s*=\s*([A-Za-z]+)([^,、]*)/g)) {
    const st = m[2].toUpperCase(); out[m[1]] = { status: ACCESS.includes(st) ? st : 'UNKNOWN', note: m[3].trim().replace(/^[（(]|[)）]$/g, '') };
  } return out;
}
function ingest(c, text, via, forcedName) {
  const r = parseBlock(text, 'STUDIO_RESULT', RESULT_KEYS);
  const sy = parseBlock(text, 'STUDIO_SYNTHESIS', SYNTH_KEYS);
  const dn = parseBlock(text, 'STUDIO_DONE', DONE_KEYS);
  if (sy) {
    if (!c.synthesisReq) return '統合の依頼をまだ出していません。';
    c.synthesis = { by: sy.fields.FROM || c.synthesisReq.to, raw: text, fields: sy.fields, missing: sy.missing, at: now(), via };
    log(c, `統合を受け取り（${c.synthesis.by}・${via}）`); return 'ok:統合';
  }
  if (dn) { c.done = { by: dn.fields.FROM || (c.next || {}).to, raw: text, fields: dn.fields, at: now(), via }; log(c, `実行結果の報告を受け取り（${via}）`); return 'ok:実行報告'; }
  let name = forcedName;
  if (!name && r) {
    const byTask = c.observers.find(n => c.tasks[n].taskId === (r.fields.TASK || '').trim());
    const from = (r.fields.FROM || '').toLowerCase();
    name = byTask || c.observers.find(n => n.toLowerCase() === from) || c.observers.find(n => from && (from.includes(n.toLowerCase()) || n.toLowerCase().includes(from)));
  }
  if (!name) return 'who';
  const t = c.tasks[name];
  if (t.result) log(c, `${name} の以前の結果（${hm(t.result.at)}）を置き換え`);
  t.result = { raw: text, fields: r ? r.fields : {}, missing: r ? r.missing : RESULT_KEYS, format: r ? (r.missing.length ? '一部' : '完全') : '形式外', at: now(), via };
  if (!t.sentAt) { t.sentAt = t.result.at; log(c, `${name} は送付の記録なしで結果が届いたため、送付済みとして扱う`); }
  const acc = parseAccess(t.result.fields.SOURCE_ACCESS);
  for (const s of c.sources) s.access[name] = acc[s.id] || { status: 'UNKNOWN', note: r ? '記載なし' : '形式外の回答' };
  log(c, `${name} の観測結果を受け取り（${via}・形式${t.result.format}）`);
  return 'ok:' + name;
}

// ---------- dispatch ----------
async function send(c, name, kind) {
  const os = obsSetting(name); const conn = Connectors[os.connector] || Connectors.manual;
  const packet = kind === 'observe' ? observePacket(c, name) : kind === 'synth' ? synthesisPacket(c, name) : nextPacket(c, name);
  const taskId = kind === 'observe' ? c.tasks[name].taskId : c.id + (kind === 'synth' ? '-SYN' : '-NEXT');
  try {
    const res = await conn.send({ caseId: c.id, taskId, observer: os, packet, proxyUrl: store.settings.proxyUrl });
    if (os.connector === 'manual') bump(c, 'copy'); else bump(c, 'autoSend');
    const stamp = { at: now(), status: res.status, connector: os.connector };
    if (kind === 'observe') { c.tasks[name].sentAt = stamp.at; c.tasks[name].sendStatus = stamp; }
    else if (kind === 'synth') c.synthesisReq = { to: name, ...stamp };
    else c.next = { to: name, packet, ...stamp };
    log(c, `${name} へ${kind === 'observe' ? '観測' : kind === 'synth' ? '統合' : '実行'}の依頼（${os.connector === 'manual' ? '手動コピー' : 'ローカル中継'}・${res.status}）`);
    if (res.status === 'shown') showPacket(packet, res.note);
    if (res.status === 'answered' && res.text) { const out = ingest(c, res.text, 'API'); bump(c, 'autoReceive'); log(c, `${name} の回答を中継から受け取り（${out}）`); }
  } catch (e) { log(c, `${name} への送付に失敗（${e.message}）`); tell(name + ' へ送れませんでした: ' + e.message); }
  save(); render(); schedulePoll();
}
async function autoDispatch(c) {
  for (const n of c.observers) if (obsSetting(n).connector !== 'manual' && !c.tasks[n].sentAt) await send(c, n, 'observe');
}
async function collectAll(c, quiet) {
  if (!store.settings.proxyUrl) return 0; let got = 0;
  const jobs = [];
  for (const n of c.observers) if (!c.tasks[n].result && c.tasks[n].sentAt && obsSetting(n).connector === 'proxy') jobs.push([n, c.tasks[n].taskId]);
  if (c.synthesisReq && !c.synthesis && obsSetting(c.synthesisReq.to).connector === 'proxy') jobs.push([c.synthesisReq.to, c.id + '-SYN']);
  if (c.next && !c.done && obsSetting(c.next.to).connector === 'proxy') jobs.push([c.next.to, c.id + '-NEXT']);
  for (const [n, taskId] of jobs) {
    try { const text = await Connectors.proxy.collect({ caseId: c.id, taskId, observer: obsSetting(n), proxyUrl: store.settings.proxyUrl });
      if (text) { const out = ingest(c, text, 'ローカル中継', taskId.endsWith('-SYN') || taskId.endsWith('-NEXT') ? undefined : n); bump(c, 'autoReceive'); got++; log(c, `${n} の結果を中継から回収（${out}）`); } }
    catch (e) { if (!quiet) log(c, `${n} の回収に失敗（${e.message}）`); }
  }
  if (got) { save(); render(); }
  return got;
}
function schedulePoll() {
  clearInterval(pollTimer); const c = cur(); if (!c || !store.settings.proxyUrl) return;
  pollTimer = setInterval(() => { const c2 = cur(); if (c2) collectAll(c2, true); }, 8000);
}
function showPacket(packet, note) {
  const d = document.createElement('dialog'); d.innerHTML = `<h2>依頼文</h2><p class="hint">${esc(note || '')}</p><textarea rows="16" readonly>${esc(packet)}</textarea><div class="row end"><button class="btn primary">閉じる</button></div>`;
  document.body.appendChild(d); d.querySelector('button').onclick = () => { d.close(); d.remove(); }; d.showModal(); d.querySelector('textarea').select();
}

// ---------- stage state ----------
function stageState(c) {
  const n = c.observers.length, sent = c.observers.filter(x => c.tasks[x].sentAt).length, got = c.observers.filter(x => c.tasks[x].result).length;
  const s = { CASE: 'done', SOURCE: c.sources.every(x => x.verified) ? 'done' : 'now', OBSERVE: got === n ? 'done' : 'now', DIFFERENCE: got >= 2 ? 'done' : '', SYNTHESIS: c.synthesis ? 'done' : '', GATE: c.gate ? 'done' : '', NEXT: c.next ? (c.done ? 'done' : 'now') : '' };
  if (!c.sources.length) s.SOURCE = 'done';
  if (got === n || (c.synthesisReq && !c.synthesis)) s.SYNTHESIS = c.synthesis ? 'done' : 'now';
  if (c.synthesis && !c.gate) s.GATE = 'now';
  if (c.gate && c.gate.decision === 'REVISE' && !c.synthesis) { s.GATE = 'done'; s.SYNTHESIS = 'now'; }
  return { s, n, sent, got };
}
function nextStep(c) {
  const { n, sent, got } = stageState(c); const acts = [];
  const pend = c.observers.filter(x => !c.tasks[x].sentAt);
  if (pend.length) {
    for (const x of pend) acts.push([obsSetting(x).connector === 'manual' ? `${x} への依頼をコピー` : `${x} へ送る`, () => send(c, x, 'observe'), true]);
    return [`各AIへ観測を依頼します（${sent}/${n} 送付済み）。手動のAIは、コピーした依頼文をそのAIに貼ってください。`, acts];
  }
  // Once synthesis has been requested with partial results, stop showing the observation wait (late answers can still be pasted).
  if (got < n && !c.synthesisReq) {
    if (store.settings.proxyUrl) acts.push(['中継から結果を回収', () => collectAll(c)]);
    acts.push(['届いた分で統合へ進む', async () => { if (await ask(`${n - got} 件が未着です。届いた ${got} 件で統合を依頼しますか？（未着は統合依頼に明記されます）`)) pickAndSend(c, 'synth'); }]);
    return [`結果を待っています（${got}/${n} 件）。届いた回答は「結果を受け取る」に貼ってください。`, acts];
  }
  if (!c.synthesisReq || (c.gate && c.gate.decision === 'REVISE' && !c.synthesis)) { acts.push(['統合を依頼する', () => pickAndSend(c, 'synth'), true]); return ['全員の観測がそろいました。統合をどのAIに頼むか選んで送ってください。', acts]; }
  if (!c.synthesis) { if (store.settings.proxyUrl) acts.push(['中継から統合を回収', () => collectAll(c)]); return [`${c.synthesisReq.to} の統合結果を待っています。届いたら「結果を受け取る」に貼ってください。`, acts]; }
  if (!c.gate || (c.gate.decision === 'REVISE' && c.synthesis && c.gate.at < c.synthesis.at)) return ['統合結果が届きました。右の「人間の判断」で APPROVE / HOLD / REVISE を選んでください。', acts];
  if (c.gate.decision === 'HOLD') return ['保留中です。確かめることが分かったら、新しい問いを始めるか、判断をやり直してください。', acts];
  if (!c.next) { acts.push(['実行タスクを送る', () => pickAndSend(c, 'next'), true]); return ['承認されました。実行タスクを実装AIへ送ってください。', acts]; }
  if (!c.done) { if (store.settings.proxyUrl) acts.push(['中継から実行報告を回収', () => collectAll(c)]); return [`${c.next.to} に実行タスクを渡しました。完了報告が届いたら貼ってください。`, acts]; }
  return ['このCaseは一周しました。記録を保存するか、次の問いを始めてください。', acts];
}
function pickAndSend(c, kind) {
  const def = kind === 'synth' ? (c.observers.includes('GPT') ? 'GPT' : c.observers[0]) : (c.observers.includes('Claude Code') ? 'Claude Code' : c.observers[0]);
  const d = document.createElement('dialog');
  d.innerHTML = `<h2>${kind === 'synth' ? '統合を頼むAI' : '実行を頼むAI'}</h2><select>${store.settings.observers.map(o => `<option${o.name === def ? ' selected' : ''}>${esc(o.name)}</option>`).join('')}</select><div class="row end"><button class="btn" value="cancel">やめる</button><button class="btn primary" value="ok">送る</button></div>`;
  document.body.appendChild(d); d.showModal();
  d.querySelectorAll('button').forEach(b => b.onclick = () => { const v = b.value, name = d.querySelector('select').value; d.close(); d.remove(); if (v === 'ok') { if (kind === 'synth' && c.gate && c.gate.decision === 'REVISE') c.synthesis = null; send(c, name, kind); } });
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
function renderObservers(c) {
  $('observers').innerHTML = c.observers.map(n => { const t = c.tasks[n], os = obsSetting(n), r = t.result;
    const st = r ? `<span class="chip ok">結果あり（${r.format}）</span>` : t.sentAt ? '<span class="chip wait">依頼済み・結果待ち</span>' : '<span class="chip">未依頼</span>';
    return `<div class="item"><div class="item-h"><b>${esc(n)}</b>${st}<span class="chip">${os.connector === 'manual' ? '手動' : 'ローカル中継'}</span>
      <button class="btn" data-send="${esc(n)}">${t.sentAt ? '依頼文を再送' : os.connector === 'manual' ? '依頼文をコピー' : '送る'}</button></div>
      ${r ? `<dl class="obs-body"><dt>観測</dt><dd>${esc(r.fields.OBSERVATION || '（ブロックなし。原文を保存）')}</dd><dt>根拠</dt><dd>${esc(r.fields.EVIDENCE || '—')}</dd><dt>不明</dt><dd>${esc(r.fields.UNKNOWN || '—')}</dd><dt>観測条件</dt><dd>${esc(r.fields.CONDITION || '—')}</dd></dl><details><summary class="hint">原文</summary><pre>${esc(r.raw)}</pre></details>` : ''}</div>`; }).join('');
  $('observers').querySelectorAll('[data-send]').forEach(b => b.onclick = () => send(c, b.dataset.send, 'observe'));
}
function autoDifferences(c) {
  const out = [];
  for (const s of c.sources) { const st = [...new Set(Object.values(s.access).map(a => a.status))];
    if (st.length > 1) out.push({ text: `${s.id} の読めた状態がAIによって違う（${Object.entries(s.access).map(([n, a]) => n + '=' + a.status).join('、')}）`, origin: 'OBSERVER', by: 'Studio（自動検出）' }); }
  const fmt = c.observers.filter(n => c.tasks[n].result && c.tasks[n].result.format === '形式外');
  if (fmt.length) out.push({ text: `${fmt.join('、')} の回答は形式外のため、項目ごとの比較ができない`, origin: 'STATE', by: 'Studio（自動検出）' });
  if (c.synthesis && c.synthesis.fields.DIFFERENCES) for (const line of c.synthesis.fields.DIFFERENCES.split(/\n|(?=- )/).map(x => x.replace(/^[-・\s]+/, '').trim()).filter(Boolean)) {
    const m = line.match(/(.*?)\|\s*ORIGIN\s*:\s*([A-Za-z]+)/i); out.push({ text: m ? m[1].trim() : line, origin: m ? m[2].toUpperCase() : '未分類', by: c.synthesis.by });
  }
  return out;
}
function renderDifference(c) {
  const done = c.observers.filter(n => c.tasks[n].result);
  if (done.length < 2) { $('difference').innerHTML = '<div class="hint">2件以上の結果がそろうと、ここに並びます。</div>'; return; }
  const diffs = autoDifferences(c);
  $('difference').innerHTML = `<table class="tbl"><tr><th></th>${done.map(n => `<th>${esc(n)}</th>`).join('')}</tr>
    ${['OBSERVATION', 'UNKNOWN', 'SOURCE_ACCESS'].map(k => `<tr><td>${k === 'OBSERVATION' ? '観測' : k === 'UNKNOWN' ? '不明' : '資料'}</td>${done.map(n => `<td>${esc(oneLine(c.tasks[n].result.fields[k]) || '—')}</td>`).join('')}</tr>`).join('')}</table>
    <div style="margin-top:8px">${diffs.map(d => `<div class="item"><span class="chip info">${esc(d.origin)}</span> ${esc(d.text)} <span class="hint">— ${esc(d.by)}</span></div>`).join('') || '<div class="hint">自動で検出できる違いはありません。内容の違いは統合で整理します。</div>'}</div>`;
}
function renderSynthesis(c) {
  if (!c.synthesis) { $('synthesis').innerHTML = `<div class="hint">${c.synthesisReq ? esc(c.synthesisReq.to) + ' に統合を依頼済み。結果を待っています。' : '観測がそろうと、統合を依頼できます。'}</div>`; return; }
  const f = c.synthesis.fields; const L = [['共通点', 'COMMON'], ['相違点', 'DIFFERENCES'], ['不明', 'UNKNOWN'], ['保留', 'HOLD'], ['実行に必要なこと', 'EXECUTION_NEEDS'], ['次のTask', 'NEXT_TASK'], ['人間が決めること', 'GATE_QUESTION']];
  $('synthesis').innerHTML = `<div class="hint">統合: ${esc(c.synthesis.by)}（${hm(c.synthesis.at)}）${c.synthesis.missing.length ? '・不足項目 ' + c.synthesis.missing.join(', ') : ''}</div><dl class="obs-body">${L.map(([j, k]) => `<dt>${j}</dt><dd>${esc(f[k] || '—')}</dd>`).join('')}</dl>`;
}
function renderGate(c) {
  if (!c.synthesis && !c.gate) { $('gate').innerHTML = '<div class="hint">統合結果が届くと、ここで判断します。</div>'; return; }
  const q = c.synthesis && c.synthesis.fields.GATE_QUESTION;
  $('gate').innerHTML = `${q ? `<div class="warn">${esc(q)}</div>` : ''}
    <textarea id="gateReason" rows="3" placeholder="判断の理由（何を採用し、何を保留し、何を直すか）">${esc(c.gate ? c.gate.reason : '')}</textarea>
    <div class="row"><button class="btn ok" data-g="APPROVE">APPROVE 採用</button><button class="btn hold" data-g="HOLD">HOLD 保留</button><button class="btn ng" data-g="REVISE">REVISE 差し戻し</button>
    ${c.gate ? `<span class="chip ${c.gate.decision === 'APPROVE' ? 'ok' : c.gate.decision === 'HOLD' ? 'wait' : 'ng'}">現在：${c.gate.decision}（${hm(c.gate.at)}）</span>` : '<span class="chip">未判断</span>'}</div>
    ${c.gateHistory.length ? `<div class="hint">これまでの判断：${c.gateHistory.map(g => g.decision + '（' + hm(g.at) + '）').join(' → ')}</div>` : ''}`;
  $('gate').querySelectorAll('[data-g]').forEach(b => b.onclick = () => {
    if (!c.synthesis) return tell('統合結果がまだありません');
    const g = { decision: b.dataset.g, reason: $('gateReason').value.trim(), at: now(), synthesisAt: c.synthesis.at };
    c.gate = g; c.gateHistory.push(g); bump(c, 'gate'); if (g.reason) bump(c, 'typed');
    if (g.decision === 'REVISE') { c.synthesisReq = null; c.synthesisPrev = c.synthesis; c.synthesis = null; }
    log(c, `人間の判断: ${g.decision}${g.reason ? '（' + oneLine(g.reason) + '）' : ''}`); save(); render();
  });
}
function renderNext(c) {
  if (!c.next) { $('next').innerHTML = `<div class="hint">${c.gate && c.gate.decision === 'APPROVE' ? '実行タスクを送れます（上の「次にすること」）。' : '人間が APPROVE すると、実装AIへの実行タスクが作られます。'}</div>`; return; }
  $('next').innerHTML = `<div class="hint">${esc(c.next.to)} へ送付（${hm(c.next.at)}）</div><details><summary class="hint">実行タスクの全文</summary><pre>${esc(c.next.packet)}</pre></details>
    ${c.done ? `<dl class="obs-body"><dt>完了報告（${esc(c.done.by || '')}）</dt><dd>${esc(c.done.fields.CHANGED || '')}</dd><dt>確かめたこと</dt><dd>${esc(c.done.fields.VERIFIED || '—')}</dd><dt>不明</dt><dd>${esc(c.done.fields.UNKNOWN || '—')}</dd><dt>次に人間が決めること</dt><dd>${esc(c.done.fields.NEXT || '—')}</dd></dl>` : '<div class="hint">完了報告を待っています。</div>'}`;
}
function renderMeter(c) {
  const m = c.metrics, carry = (m.copy || 0) + (m.paste || 0) + (m.drop || 0);
  $('meter').innerHTML = `<div><b>${carry}</b><span>人間の運搬（コピー＋貼り付け＋ファイル）</span></div><div><b>${m.copy || 0}</b><span>コピー</span></div><div><b>${(m.paste || 0) + (m.drop || 0)}</b><span>貼り付け・ドロップ</span></div><div><b>${(m.autoSend || 0) + (m.autoReceive || 0)}</b><span>Studioが自動で運んだ回数</span></div><div><b>${(m.typed || 0) + (m.gate || 0)}</b><span>人間の入力・判断</span></div>`;
}

// ---------- settings ----------
function renderSettings() {
  $('proxyUrl').value = store.settings.proxyUrl || '';
  $('connRows').innerHTML = store.settings.observers.map((o, i) => `<tr><td>${esc(o.name)}</td><td><select data-i="${i}" data-k="connector"><option value="manual"${o.connector === 'manual' ? ' selected' : ''}>手動</option><option value="proxy"${o.connector === 'proxy' ? ' selected' : ''}>ローカル中継</option></select></td>
    <td><select data-i="${i}" data-k="route">${['agent', 'api:openai', 'api:anthropic', 'api:gemini', 'mock'].map(r => `<option value="${r}"${o.route === r ? ' selected' : ''}>${r === 'agent' ? 'ファイル受け渡し（Claude Code・Codex等）' : r === 'mock' ? 'テスト用の模擬応答' : 'API ' + r.slice(4)}</option>`).join('')}</select></td></tr>`).join('');
  $('connRows').querySelectorAll('select').forEach(s => s.onchange = () => { store.settings.observers[+s.dataset.i][s.dataset.k] = s.value; save(); });
}

// ---------- events ----------
function bind() {
  $('startBtn').onclick = () => { const q = $('qInput').value.trim(); if (!q) { $('qInput').focus(); return; }
    const names = [...$('obsPick').querySelectorAll('input:checked')].map(x => x.value); if (!names.length) return tell('AIを1つ以上選んでください');
    $('qInput').value = ''; createCase(q, names); };
  $('newCaseBtn').onclick = () => { store.currentId = null; save(); render(); $('qInput').focus(); };
  $('caseList').onclick = e => { const b = e.target.closest('[data-id]'); if (!b) return; store.currentId = b.dataset.id; save(); render(); schedulePoll(); };
  $('pasteBtn').onclick = () => doPaste($('pasteBox').value, '貼り付け');
  $('pasteBox').addEventListener('dragover', e => e.preventDefault());
  $('pasteBox').addEventListener('drop', async e => { e.preventDefault(); const f = e.dataTransfer.files[0]; if (f) doPaste(await f.text(), 'ファイル'); });
  $('srcAddBtn').onclick = () => { const c = cur(), v = $('srcAdd').value.trim(); if (!v) return;
    const found = extractSources(v); const list = found.length ? found : [{ id: '', kind: 'テキスト', ref: v, claimedHash: null, verified: null, access: {} }];
    for (const s of list) { s.id = 'S' + (c.sources.length + 1); c.sources.push(s); log(c, `資料 ${s.id} を追加`); }
    bump(c, 'typed'); $('srcAdd').value = ''; save(); render(); verifySources(c); };
  $('srcFile').onchange = async e => { const c = cur(), f = e.target.files[0]; if (!f) return;
    const h = await sha256Hex(await f.arrayBuffer());
    let s = c.sources.find(x => x.ref.endsWith(f.name)); if (!s) { s = { id: 'S' + (c.sources.length + 1), kind: 'ファイル', ref: f.name, claimedHash: null, verified: null, access: {} }; c.sources.push(s); }
    if (h) { s.verified = { sha256: h, size: f.size, by: 'Studio（手元のファイル）', at: now() }; log(c, `${s.id} を手元のファイルで確認`); } else s.verifyNote = 'このブラウザでは計算できません（http://localhost で開いてください）';
    bump(c, 'drop'); e.target.value = ''; save(); render(); };
  $('exportBtn').onclick = () => { const c = cur(); download(c.id + '.json', JSON.stringify({ format: 'ats-operational-case', version: 1, exportedAt: now(), case: c }, null, 2), 'application/json'); };
  $('mdBtn').onclick = () => { const c = cur(); download(c.id + '.md', caseMd(c), 'text/markdown'); };
  $('importFile').onchange = async e => { const f = e.target.files[0]; e.target.value = ''; if (!f) return;
    try { const o = JSON.parse(await f.text()); if (o.format !== 'ats-operational-case' || !o.case || !o.case.id) throw new Error('このStudioの記録JSONではありません');
      let c = o.case; if (store.cases[c.id] && !await ask(c.id + ' は既にあります。読み込んだ内容で置き換えますか？')) return;
      store.cases[c.id] = c; if (!store.order.includes(c.id)) store.order.unshift(c.id); store.currentId = c.id;
      if (saveBlocked && await ask('保存が止まっています。読み込んだ内容で保存を再開しますか？（読めなかった保存データは上書きされます）')) { saveBlocked = false; $('saveWarn').hidden = true; }
      log(c, '記録JSONから読み込み'); save(); render(); } catch (err) { tell('読み込めません: ' + err.message); } };
  $('settingsBtn').onclick = () => { renderSettings(); $('settings').showModal(); };
  // Save the relay URL as it is typed, so re-rendering the dialog (e.g. adding an AI) never drops it.
  $('proxyUrl').oninput = () => { store.settings.proxyUrl = $('proxyUrl').value.trim(); save(); };
  $('settings').addEventListener('close', () => { save(); render(); schedulePoll(); });
  $('proxyTest').onclick = async () => { try { const j = await proxyHealth($('proxyUrl').value.trim()); $('proxyMsg').textContent = `接続OK（使えるAPI: ${j.providers.join(', ') || 'なし'}／受け渡しフォルダ: ${j.exchange}）`; } catch (e) { $('proxyMsg').textContent = '接続できません: ' + e.message; } };
  $('newObsBtn').onclick = () => { const v = $('newObs').value.trim(); if (!v || store.settings.observers.some(o => o.name === v)) return; store.settings.observers.push({ name: v, connector: 'manual', route: 'manual' }); $('newObs').value = ''; save(); renderSettings(); render(); };
}
function doPaste(text, via) {
  const c = cur(); if (!text.trim()) return;
  let out = ingest(c, text, via);
  if (out === 'who') {
    $('pasteMsg').innerHTML = '誰の結果か判定できません。選んでください：' + c.observers.map(n => `<button class="btn" data-who="${esc(n)}">${esc(n)}</button>`).join(' ');
    $('pasteMsg').querySelectorAll('[data-who]').forEach(b => b.onclick = () => { ingest(c, text, via, b.dataset.who); finishPaste(c, via, 'ok:' + b.dataset.who); });
    return;
  }
  if (!out.startsWith('ok')) { $('pasteMsg').textContent = out; return; }
  finishPaste(c, via, out);
}
function finishPaste(c, via, out) { bump(c, via === 'ファイル' ? 'drop' : 'paste'); $('pasteBox').value = ''; $('pasteMsg').textContent = '取り込みました（' + out.slice(3) + '）'; save(); render(); }
function download(name, text, type) { const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([text], { type })); a.download = name; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 1000); }
function caseMd(c) {
  const L = [`# ${c.id}`, '', '## 問い', c.question, '', '## SOURCE'];
  for (const s of c.sources) L.push(`- ${s.id} ${s.kind} ${s.ref} — ${srcState(s)[0]}${s.verified ? ' sha256 ' + s.verified.sha256 : ''}｜読めたか: ${Object.entries(s.access).map(([n, a]) => n + '=' + a.status).join(', ') || '報告なし'}`);
  L.push('', '## OBSERVE');
  for (const n of c.observers) { const r = c.tasks[n].result; L.push(`### ${n}`, r ? `受領 ${r.at}（${r.via}・形式${r.format}）` : '未着', ''); if (r) L.push('```text', r.raw, '```', ''); }
  L.push('## DIFFERENCE'); for (const d of autoDifferences(c)) L.push(`- [${d.origin}] ${d.text}（${d.by}）`);
  L.push('', '## SYNTHESIS', c.synthesis ? '```text\n' + c.synthesis.raw + '\n```' : '（なし）', '', '## HUMAN GATE');
  for (const g of c.gateHistory) L.push(`- ${g.decision} ${g.at} ${g.reason || ''}`);
  L.push('', '## NEXT', c.next ? '```text\n' + c.next.packet + '\n```' : '（なし）', c.done ? '\n### 完了報告\n```text\n' + c.done.raw + '\n```' : '', '', '## 経過');
  for (const l of c.log) L.push(`- ${l.at} ${l.t}`);
  return L.join('\n');
}

load(); bind(); render(); schedulePoll();
