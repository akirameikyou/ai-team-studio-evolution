'use strict';
// AI Team Studio — reply parsing v2 (stage 1a). Pure functions only: no DOM, no storage, no network.
// Spec: STUDIO-PLAN_解析ルールv2_期待仕様書_v0.4.txt (§1, §2, §10, §13, §14) with D1–D8 as decided on 2026-10-12,
// plus D10 (identity keys alone = NO_CONTENT) and D11 = A (duplicates are line-start repeats only) from stage 1a-r2.
// Three steps: recognise every opening tag as a candidate, judge each candidate on its own, then adopt only a unique valid one.
(function (global) {
  const RULE_VERSION = 'v2';
  const TEMPLATE_TABLE_VERSION = 'TEMPLATE-TABLE-2026-10-12-a';
  const IDENTITY_KEYS = ['CASE', 'TASK', 'FROM'];
  const MIN_DISTINCT_KEYS = 3;                       // D1

  // Template wording copied from app.js observePacket / synthesisPacket / nextPacket
  // (working tree, SHA-256 a1508b70…03442). SOURCE_ACCESS is matched by grammar instead (see isTemplateValue).
  const TEMPLATES = {
    STUDIO_RESULT: {
      CONDITION: 'モデル / 使えた道具 / ファイル可否 / Web可否 / 事前知識の有無',
      OBSERVATION: '観測したこと・答え',
      EVIDENCE: '根拠（DIRECT/REPORTED/INFERENCE）',
      UNKNOWN: '確かめられなかったこと'
    },
    STUDIO_SYNTHESIS: {
      COMMON: '共通して言えること',
      DIFFERENCES: '1行に1つ「内容 | ORIGIN: 層」',
      UNKNOWN: 'まだ言えないこと',
      HOLD: '判断を保留すべきこと',
      EXECUTION_NEEDS: '実行に必要な情報・条件',
      NEXT_TASK: '実装・作業AIへの具体的な依頼（作業単位を1つ）',
      GATE_QUESTION: '人間が決めるべきこと'
    },
    STUDIO_DONE: {
      CHANGED: '変更したもの（ファイル・commit等）',
      VERIFIED: '実際に確かめたこと',
      UNKNOWN: '確かめられなかったこと',
      NEXT: '次に人間が決めること'
    }
  };
  const SA_ITEM = 'S\\d+=DIRECT\\|READABLE\\|INACCESSIBLE\\|UNKNOWN';
  const SOURCE_ACCESS_TEMPLATE = new RegExp('^(?:' + SA_ITEM + '(?:, ' + SA_ITEM + ')*|N/A)\\(理由\\)$');

  // N-1: one character for one character, so offsets in the original text stay valid.
  const normalizeMarks = s => String(s ?? '').replace(/［/g, '[').replace(/］/g, ']').replace(/：/g, ':');
  // N-2: only for comparing values with the template table.
  const normalizeForCompare = s => String(s).normalize('NFKC').trim().replace(/\s+/g, ' ');
  const escapeRe = s => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

  function isTemplateValue(tag, key, value) {
    const v = normalizeForCompare(value);
    if (tag === 'STUDIO_RESULT' && key === 'SOURCE_ACCESS') return SOURCE_ACCESS_TEMPLATE.test(v);
    const tpl = (TEMPLATES[tag] || {})[key];
    return tpl !== undefined && v === normalizeForCompare(tpl);
  }
  const isParenthesized = value => /^\([\s\S]*\)$/.test(normalizeForCompare(value));

  // Step 1: every "[TAG]" opens a candidate. The body ends at the next "[/TAG]" if that comes before the next "[TAG]";
  // otherwise at the next "[TAG]" or the end of the text (unclosed). A closing tag with no open candidate before it is ignored.
  function findCandidates(t, tag) {
    const openRe = new RegExp(escapeRe('[' + tag + ']'), 'gi'), closeRe = new RegExp(escapeRe('[/' + tag + ']'), 'gi');
    const opens = []; let m;
    while ((m = openRe.exec(t))) opens.push(m.index);
    const openLen = tag.length + 2;
    return opens.map((start, i) => {
      const bodyStart = start + openLen, nextOpen = i + 1 < opens.length ? opens[i + 1] : -1;
      closeRe.lastIndex = bodyStart; const c = closeRe.exec(t);
      const closed = !!c && (nextOpen < 0 || c.index < nextOpen);
      const end = closed ? c.index : (nextOpen >= 0 ? nextOpen : t.length);
      return { id: 'c' + (i + 1), start, end, closed, followedByTag: !closed && nextOpen >= 0, body: t.slice(bodyStart, end) };
    });
  }

  // Field extraction inside one candidate body: the same walk as version B (template order, line-start preferred,
  // joined lines still split), plus later line-start repeats of a key as value boundaries so they are detected as duplicates.
  function extractFields(body, keys) {
    const keyPos = h => h.a + h.lead;
    const find = (k, from, lineOnly) => {
      const re = new RegExp((lineOnly ? '(?:^|\\n)[ \\t]*' : '(?:^|\\s)') + '(' + k + ')\\s*:', 'gi'); re.lastIndex = from;
      const h = re.exec(body); return h ? { k, a: h.index, s: h.index + h[0].length, lead: h[0].search(/[A-Za-z]/) } : null;
    };
    const hits = []; let pos = 0;
    const anyKey = new RegExp('(?:^|\\s)(' + keys.join('|') + ')\\s*:', 'i');
    for (const k of keys) {
      const inl = find(k, pos, false), line = find(k, pos, true);
      const pick = line && inl && line.a > inl.a && !anyKey.test(body.slice(inl.s, line.a)) ? line : inl;
      if (pick) { hits.push(pick); pos = pick.s; }
    }
    for (const k of keys) if (!hits.some(x => x.k === k)) { const h = find(k, 0, true); if (h && !hits.some(x => h.a >= x.a && h.a < x.s)) hits.push(h); }
    const taken = new Set(hits.map(keyPos)), markers = [];
    const lineRe = new RegExp('(?:^|\\n)[ \\t]*(' + keys.join('|') + ')\\s*:', 'gi'); let m;
    while ((m = lineRe.exec(body))) {
      const mk = { k: m[1].toUpperCase(), a: m.index, s: m.index + m[0].length, lead: m[0].search(/[A-Za-z]/), dup: true };
      if (!taken.has(keyPos(mk)) && hits.some(x => x.k === mk.k)) markers.push(mk);
    }
    const all = hits.concat(markers).sort((x, y) => x.a - y.a);
    const valueOf = i => body.slice(all[i].s, i + 1 < all.length ? all[i + 1].a : body.length).trim();
    // X-2 / D7: whichever occurrence of a key comes first in the body supplies the value; later ones are duplicates.
    const fields = {}, dups = {};
    all.forEach((x, i) => {
      if (!(x.k in fields)) fields[x.k] = valueOf(i);
      else (dups[x.k] = dups[x.k] || []).push(valueOf(i));
    });
    return { fields, dups };
  }

  function unrecognizedLabels(body, keys) {
    const out = [], re = /(?:^|\n)[ \t]*([^\s:[\]\/]{1,40}?)\s*:(?!\/)/g; let m;
    const known = new Set(keys.map(k => k.toUpperCase()));
    while ((m = re.exec(body))) { const l = m[1]; if (!known.has(l.toUpperCase()) && !/^\d+$/.test(l) && !out.includes(l)) out.push(l); }
    return out;
  }

  // Step 2: judge one candidate.
  function judge(c, tag, keys) {
    const { fields, dups } = extractFields(c.body, keys);
    const keySet = Object.keys(fields);
    c.keySet = keySet;
    c.placeholder = {};
    for (const k of keySet) {
      if (isTemplateValue(tag, k, fields[k])) c.placeholder[k] = 'TEMPLATE';
      else if (isParenthesized(fields[k])) c.placeholder[k] = 'PARENTHESIZED';
    }
    if (keySet.length < MIN_DISTINCT_KEYS) { c.verdict = 'NOT_STRUCTURAL'; return { fields, dups }; }
    const nonIdentity = keySet.filter(k => !IDENTITY_KEYS.includes(k));
    // D10: identity keys alone carry no answer. D4: every non-identity value is a template value or parenthesized.
    c.verdict = nonIdentity.length === 0 ? 'NO_CONTENT' : nonIdentity.every(k => c.placeholder[k]) ? 'TEMPLATE' : 'VALID';
    return { fields, dups };
  }

  function parseBlockV2(text, tag, keys) {
    const t = normalizeMarks(text);
    const cands = findCandidates(t, tag);
    if (!cands.length) return null;                                    // NO_BLOCK
    const extracted = cands.map(c => judge(c, tag, keys));
    const valid = cands.filter(c => c.verdict === 'VALID');
    const base = { ruleVersion: RULE_VERSION, templateTableVersion: TEMPLATE_TABLE_VERSION };
    const failed = reason => {
      valid.forEach(c => { c.verdict = 'VALID_NOT_SELECTED'; });
      return { ...base, status: 'PARSE_FAILED', reason, fields: {}, missing: keys.slice(), warnings: [], conflicts: [], selected: null, candidates: diag(cands) };
    };
    // X-4: count valid candidates first, so AMBIGUOUS wins over the other reasons.
    if (valid.length >= 2) return failed('AMBIGUOUS_MULTIPLE_BLOCKS');
    if (valid.length === 0) {
      const has = v => cands.some(c => c.verdict === v);
      return failed(has('TEMPLATE') ? 'TEMPLATE_ONLY' : has('NO_CONTENT') ? 'NO_CONTENT_FIELDS' : 'NO_STRUCTURAL_CANDIDATE');
    }
    const sel = valid[0];
    if (!sel.closed && sel.followedByTag) return failed('UNCLOSED_FOLLOWED_BY_TAG');
    sel.verdict = 'SELECTED';
    const { fields, dups } = extracted[cands.indexOf(sel)];
    const warnings = [], conflicts = [];
    if (!sel.closed) warnings.push('UNCLOSED');
    for (const k of Object.keys(dups)) {
      warnings.push('DUPLICATE_KEY:' + k);
      if (dups[k].some(v => v !== fields[k])) conflicts.push(k);           // D7: the first value is kept
    }
    for (const k of Object.keys(sel.placeholder)) warnings.push((sel.placeholder[k] === 'TEMPLATE' ? 'TEMPLATE_VALUE:' : 'PARENTHESIZED_VALUE:') + k);
    const labels = unrecognizedLabels(sel.body, keys);
    labels.forEach(l => warnings.push('UNRECOGNIZED_LABEL:' + l));
    if (labels.length) warnings.push('CLASSIFICATION_UNCERTAIN');
    const missing = keys.filter(k => !(k in fields));
    const status = !sel.closed ? 'UNCONFIRMED' : missing.length ? 'PARTIAL' : 'OK';
    return { ...base, status, reason: null, fields, missing, warnings, conflicts, selected: sel.id, candidates: diag(cands) };
  }

  const diag = cands => cands.map(c => ({ id: c.id, start: c.start, end: c.end, closed: c.closed, keySet: c.keySet, placeholder: c.placeholder, verdict: c.verdict }));

  // D8 = A: which items a person must confirm before the result may be used.
  function requiredConfirmations(r, opts = {}) {
    const req = [];
    if (!r || !r.status) return req;
    if (r.status === 'UNCONFIRMED') req.push('UNCLOSED');
    if (r.status === 'NO_BLOCK' && opts.noBlockNeedsConfirmation) req.push('NO_BLOCK');   // D9 = B, stage 2
    for (const w of r.warnings || []) {
      if (w.startsWith('TEMPLATE_VALUE:') || w === 'CLASSIFICATION_UNCERTAIN') req.push(w);
      else if (w.startsWith('DUPLICATE_KEY:') && (r.conflicts || []).includes(w.slice('DUPLICATE_KEY:'.length))) req.push(w);
    }
    return req;
  }

  // §10: the single eligibility rule. Results without status are the old format and stay usable as before.
  function eligible(r, opts = {}) {
    if (!r) return false;
    if (!r.status) return true;
    if (r.status === 'PARSE_FAILED') return false;
    const done = new Set((r.confirmation && r.confirmation.items) || []);
    return requiredConfirmations(r, opts).every(x => done.has(x));
  }

  const api = { RULE_VERSION, TEMPLATE_TABLE_VERSION, parseBlockV2, eligible, requiredConfirmations };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  global.ParseV2 = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
