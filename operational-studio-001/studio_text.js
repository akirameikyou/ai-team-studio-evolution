'use strict';
// AI Team Studio — stage 1d text helpers. Pure functions over strings (no DOM, no storage, no network), testable in Node.
//   extractSources(text, { bareNames })  SOURCE candidates in a text (1d-1)
//   splitDifferences(value)              items of a synthesis DIFFERENCES value, for display only (1d-2)
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
    for (const m of t.matchAll(/[A-Za-z]:\\[^\s"'<>|]+/g)) add('ファイル', m[0]);
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

  global.StudioText = { extractSources, splitDifferences, LAYERS };
})(typeof globalThis !== 'undefined' ? globalThis : this);
