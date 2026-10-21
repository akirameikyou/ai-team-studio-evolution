'use strict';
// AI Team Studio — stage 1d text helpers. Pure functions over strings (no DOM, no storage, no network), testable in Node.
//   extractSources(text, { bareNames })  SOURCE candidates in a text (1d-1)
//   splitDifferences(value)              items of a synthesis DIFFERENCES value, for display only (1d-2)
//   relaySafeName / relayPaths / agentInstruction   relay file names, paths and the text to give an AI (1d-3)
//   textSource / sourceBodies            premise text held by the Studio as a SOURCE, and its block in requests (2-1A)
(function (global) {
  // 1d-1: URLs and absolute paths are always taken. Bare file names ("report.pdf") are taken only when a person typed them
  // into "add SOURCE" (bareNames: true, the default). From the question they are not: a file name mentioned in a question is
  // not a document given to the AIs (10/14: "from-Codex.md" became SOURCE S1 and the observer went looking for it).
  function extractSources(text, opts) {
    const bareNames = !opts || opts.bareNames !== false;
    const out = [], seen = new Set();
    const add = (kind, ref) => { if (seen.has(ref)) return; seen.add(ref); out.push({ id: 'S' + (out.length + 1), kind, ref, claimedHash: null, verified: null, access: {} }); };
    const t = String(text || '');
    // A URL ends at whitespace, < > " ' ( ), or the first non-ASCII character (Japanese text often follows a URL without a space).
    for (const m of t.matchAll(/https?:\/\/[^\s<>"'）()」\u0080-\uffff]+/g)) add(/github(usercontent)?\.com/.test(m[0]) ? 'GitHub' : 'URL', m[0].replace(/[。、.,]+$/, ''));
    // A path ends at whitespace, " ' < > |, or full-width punctuation that follows it in Japanese text（）「」、。 etc.
    // (10/21: "C:\GitHub\repo）を、読み取り専用で…" became one SOURCE). Japanese folder names inside a path are kept.
    for (const m of t.matchAll(/[A-Za-z]:\\[^\s"'<>|\u3000\u3001\u3002\uff08\uff09\u300c\u300d\u300e\u300f\u3010\u3011\uff0c\uff0e\uff1a\uff1b\uff01\uff1f]+/g)) add('ファイル', m[0]);
    if (bareNames) for (const m of t.matchAll(/\b[\w.-]+\.(zip|pdf|md|json|html|csv|txt|docx|xlsx)\b/gi)) if (![...seen].some(r => r.includes(m[0]))) add('ファイル', m[0]);
    const hashes = [...t.matchAll(/\b[0-9a-f]{64}\b/gi)].map(m => m[0].toLowerCase());
    if (hashes.length && out.length) out[0].claimedHash = hashes[0];
    return out;
  }

  // 1d-2: items are separated by newlines or "- " bullets (as before). Inside one line, a "| ORIGIN: <layer>" marker ends an
  // item only when the line has two or more markers and every marker names a known layer; such items carry auto: true
  // ("split automatically, no line breaks"). Anything that cannot be split with certainty stays one item, unclassified, with
  // its text unchanged. Nothing is dropped: text after the last marker becomes its own unclassified item, an empty item shows
  // as "（内容なし）". Display only: the stored synthesis is never changed.
  // A layer name is the whole word after "ORIGIN:" (letters, digits, _ and -), compared with the known layers exactly.
  // A line with a single marker is not split, so its layer is shown as written, known or not.
  const LAYERS = ['SOURCE', 'CONTEXT', 'STATE', 'OBSERVER', 'EVIDENCE', 'REASONING', 'JUDGMENT'];
  const MARK = /\|\s*ORIGIN\s*:\s*([A-Za-z0-9_-]+)/gi;
  const LABEL = /^\s*DIFFERENCES\s*:\s*/i;
  function splitDifferences(value) {
    const out = [];
    if (!value) return out;
    const pieces = String(value).split(/\n|(?=- )/).map(x => x.replace(/^[-・\s]+/, '').trim()).filter(Boolean);
    for (const piece of pieces) {
      const marks = [...piece.matchAll(MARK)];
      if (!marks.length) { out.push({ text: piece, origin: '未分類', auto: false }); continue; }
      const auto = marks.length > 1;
      if (auto && !marks.every(m => LAYERS.includes(m[1].toUpperCase()))) { out.push({ text: piece, origin: '未分類', auto: false }); continue; }
      let pos = 0;
      for (const m of marks) {
        const seg = piece.slice(pos, m.index).replace(LABEL, '').trim();
        out.push({ text: seg || '（内容なし）', origin: m[1].toUpperCase(), auto });
        pos = m.index + m[0].length;
      }
      const rest = piece.slice(pos).replace(LABEL, '').trim();
      if (rest) out.push({ text: rest, origin: '未分類', auto });
    }
    return out;
  }


  // 1d-3: where the relay (proxy/studio_proxy.py) puts the request and expects the reply, and what to tell the AI.
  // relaySafeName mirrors the relay's safe(): re.sub(r'[^\w.-]+', '_', part)[:80], and refuses '', '.', '..'.
  // (Python's \w is Unicode letters, digits and _; the 80 is counted in code points.)
  function relaySafeName(part) {
    const s = Array.from(String(part == null ? '' : part).replace(/[^\p{L}\p{N}_.-]+/gu, '_')).slice(0, 80).join('');
    return !s || s === '.' || s === '..' ? null : s;
  }
  function relayPaths(exchangeDir, caseId, taskId, aiName) {
    const c = relaySafeName(caseId), t = relaySafeName(taskId), a = relaySafeName(aiName);
    if (!c || !t || !a) return null;
    const dir0 = String(exchangeDir || '').replace(/[\\/]+$/, '');
    const relative = !dir0, base = relative ? 'proxy/exchange' : dir0, sep = !relative && base.includes('\\') ? '\\' : '/';
    const dir = [base, c, t].join(sep);
    return { dir, toFile: dir + sep + 'to-' + a + '.md', fromFile: dir + sep + 'from-' + a + '.md', relative };
  }
  function agentInstruction(p) {
    if (!p) return '';
    return ['AI Team Studio からの依頼です。次の依頼ファイルを読んでください：', p.toFile, '',
      '依頼文の指示に従い、同じフォルダに次の結果ファイルを新しく書いてください（書いてよいのは、このファイルだけです）：', p.fromFile, '',
      // 2-1 follow-up: the relay's 2-second wait is only a heuristic; an atomic publish by the writer is what prevents half-read replies.
      '書くときは、まず同じフォルダに一時ファイル（結果ファイルの名前の後ろに .tmp を付けた名前）で全部書き、書き終えてから結果ファイルの名前に変えてください。書きかけの結果ファイルを見せないためです。', '',
      'ほかのファイルは作成・変更・削除しないでください。commit・push もしないでください。',
      '依頼ファイルが無いとき、または依頼文の TASK がフォルダ名と合わないときは、何も書かずに止まって知らせてください。'].join('\n');
  }

  // 2-1A: a SOURCE whose full text the Studio holds (a premise pasted by the person). The text goes into every request in
  // full, between delimiters, as one element so that blank lines inside it survive the filters of the packet builders.
  // sha256 / size are computed by the caller (Web Crypto is async); without them the SOURCE stays unverified.
  const TEXT_KIND = '本文', TEXT_MAX = 200000;
  function textSource(id, title, text, sha256, size, at) {
    const t = String(text == null ? '' : text);
    if (!t.trim()) return { error: '本文が空です' };
    // The limit counts UTF-16 code units (String.length): a character outside the BMP counts as 2. This errs on the safe
    // side for the browser's storage quota, which is also measured in code units (Codex review of 12d366f).
    if (t.length > TEXT_MAX) return { error: `本文が長すぎます（${t.length}文字。上限 ${TEXT_MAX}文字）` };
    // The hash and size come from the caller; the size is checked against the text here (the hash cannot be, synchronously).
    if (sha256 && typeof TextEncoder !== 'undefined' && new TextEncoder().encode(t).length !== size) return { error: '本文の大きさが記録と合いません' };
    return { id, kind: TEXT_KIND, ref: String(title || '').trim() || '前提資料', text: t, claimedHash: null,
      verified: sha256 ? { sha256, size, by: 'Studio（本文を保存）', at } : null, access: {} };
  }
  function sourceBodies(sources) {
    const list = (sources || []).filter(s => s.kind === TEXT_KIND && typeof s.text === 'string');
    if (!list.length) return [];
    const out = ['', '■ 資料本文（Studio が保存した全文。そのまま読んでください）'];
    for (const s of list) out.push(`--- ${s.id}「${s.ref}」${s.verified ? ` SHA-256 ${s.verified.sha256}` : ''} ---`, s.text, `--- ${s.id} ここまで ---`);
    return out;
  }

  // A text SOURCE already registered: same text, or same SHA-256 when both have one. The text itself is compared as well,
  // because without Web Crypto there is no hash to compare (Codex review, CASE-20261021-003).
  function isDuplicateText(sources, s) {
    return (sources || []).some(x => (x.kind === TEXT_KIND && x.text === s.text)
      || (!!s.verified && !!x.verified && x.verified.sha256 === s.verified.sha256));
  }

  // 2-B: a Case ID that does not repeat after the browser's storage is lost. CASE-YYYYMMDD-NNN-xxxxxx: the date and a
  // counter for people, and a random suffix (lowercase letters and digits, safe as a Windows folder name) so that a
  // counter that restarts at 001 cannot reuse an earlier Case's relay folder (10/21: an old from-Codex.md would have
  // been taken as the new Case's result). `suffix` is supplied by the caller (crypto.getRandomValues in the app).
  function caseIdFor(date, existingIds, suffix) {
    const d = date instanceof Date ? date : new Date(date);
    const ymd = d.getFullYear() + String(d.getMonth() + 1).padStart(2, '0') + String(d.getDate()).padStart(2, '0');
    const sfx = String(suffix || '').toLowerCase().replace(/[^a-z0-9]/g, '');
    if (sfx.length < 6) return null;
    // Old IDs (CASE-YYYYMMDD-NNN) and new ones both count by their date-and-counter part.
    const known = new Set([...(existingIds || [])].map(x => (String(x).match(/^CASE-\d{8}-\d{3}/) || [''])[0]));
    let n = 1; while (known.has(`CASE-${ymd}-${String(n).padStart(3, '0')}`)) n++;
    return `CASE-${ymd}-${String(n).padStart(3, '0')}-${sfx.slice(0, 6)}`;
  }

  // 2-C: a record copy read back from the relay, checked with the same rules app.js load() applies to localStorage
  // (version 1, cases an object), plus order / settings so a restored record can be rendered.
  function parseStoreSnapshot(text) {
    let o;
    try { o = JSON.parse(String(text)); } catch (e) { return { error: 'JSON として読めません' }; }
    if (!o || typeof o !== 'object' || o.version !== 1 || !o.cases || typeof o.cases !== 'object' || Array.isArray(o.cases)) return { error: 'スタジオの記録（version 1）ではありません' };
    if (!Array.isArray(o.order) || !o.settings || typeof o.settings !== 'object' || !Array.isArray(o.settings.observers)) return { error: '記録の形が足りません（order / settings）' };
    const missing = o.order.filter(id => !o.cases[id]);
    if (missing.length) return { error: '一覧にある Case が記録にありません：' + missing.join(', ') };
    return { store: o };
  }

  // 2-E: the default settings served by the relay (proxy/studio.config.json), checked before use. Returns the settings
  // object to store, or { error }. Only a relay on this machine is accepted; connectors and routes must be known values.
  const CONNECTORS = ['manual', 'proxy'], ROUTES = ['agent', 'api:openai', 'api:anthropic', 'api:gemini', 'mock', 'manual'];
  function studioSettingsFrom(cfg) {
    if (!cfg || typeof cfg !== 'object' || cfg.version !== 1) return { error: '初期設定の形が違います（version 1 ではありません）' };
    if (!/^http:\/\/(127\.0\.0\.1|localhost)(:\d+)?\/?$/.test(String(cfg.proxyUrl || ''))) return { error: '中継の URL は、このパソコンのもの（127.0.0.1 / localhost）だけを使えます' };
    if (!Array.isArray(cfg.observers) || !cfg.observers.length) return { error: '観測者の一覧がありません' };
    const names = new Set(), observers = [];
    for (const o of cfg.observers) {
      const name = String((o && o.name) || '').trim();
      if (!name || names.has(name)) return { error: '観測者の名前が空か重複しています：' + name };
      if (!CONNECTORS.includes(o.connector) || !ROUTES.includes(o.route)) return { error: name + ' の渡し方が分かりません' };
      names.add(name); observers.push({ name, connector: o.connector, route: o.route });
    }
    return { settings: { proxyUrl: String(cfg.proxyUrl).replace(/\/$/, ''), observers } };
  }

  // A Markdown code fence longer than the longest run of backticks in the text (at least three).
  function fenceFor(text) {
    const runs = String(text == null ? '' : text).match(/`+/g) || [];
    return '`'.repeat(Math.max(3, ...runs.map(r => r.length + 1)));
  }

  global.StudioText = { extractSources, splitDifferences, LAYERS, relaySafeName, relayPaths, agentInstruction, TEXT_KIND, TEXT_MAX, textSource, sourceBodies, isDuplicateText, fenceFor, caseIdFor, parseStoreSnapshot, studioSettingsFrom };
})(typeof globalThis !== 'undefined' ? globalThis : this);
