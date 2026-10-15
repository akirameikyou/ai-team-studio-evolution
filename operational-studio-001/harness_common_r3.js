'use strict';
// Shared checks for stage 1a-r3 (copy of harness_common.js with conflicts always compared):
// used by harness_v2r3.html and mutation_runner_r3.html so both judge the same way.
(function (global) {
  const sameSet = (x, y) => JSON.stringify([...(x || [])].sort()) === JSON.stringify([...(y || [])].sort());
  const sameMap = (x, y) => JSON.stringify(Object.entries(x || {}).sort()) === JSON.stringify(Object.entries(y || {}).sort());

  function fieldsMatch(exp, got, why) {
    for (const [k, v] of Object.entries(exp)) {
      if (typeof v !== 'string') { why.push('non-exact expectation for ' + k); continue; }   // r2: no prefix matching
      if (got[k] !== v) why.push('field ' + k + ' = ' + JSON.stringify(got[k]));
    }
    for (const k of Object.keys(got)) if (!(k in exp)) why.push('unexpected field ' + k);
  }

  // Start/end are checked by their defining properties instead of hand-written numbers (except positionCases).
  function boundaryWhy(input, tag, cands) {
    const why = [], t = input.replace(/［/g, '[').replace(/］/g, ']');
    const open = '[' + tag.toLowerCase() + ']', close = '[/' + tag.toLowerCase() + ']', low = s => s.toLowerCase();
    const opens = []; for (let i = 0; (i = low(t).indexOf(open, i)) >= 0; i++) opens.push(i);
    if (cands.length !== opens.length) why.push('candidate count ' + cands.length + ' != open tags ' + opens.length);
    cands.forEach((c, i) => {
      if (c.id !== 'c' + (i + 1)) why.push('id ' + c.id + ' at ' + i);
      if (c.start !== opens[i]) why.push(c.id + ' start ' + c.start + ' != ' + opens[i]);
      const atClose = low(t.slice(c.end, c.end + close.length)) === close;
      const nextOpen = i + 1 < opens.length ? opens[i + 1] : -1;
      if (c.closed !== atClose) why.push(c.id + ' closed flag does not match text at end');
      if (!c.closed && c.end !== (nextOpen >= 0 ? nextOpen : t.length)) why.push(c.id + ' unclosed end ' + c.end);
      if (c.closed && low(t.slice(opens[i] + open.length, c.end)).includes(close)) why.push(c.id + ' closed at a later close tag');
      if (c.closed && nextOpen >= 0 && c.end > nextOpen) why.push(c.id + ' closed after next open');
    });
    return why;
  }

  function judge(c, got, versions) {
    const exp = c.expected, why = [];
    if (exp === null) { if (got !== null) why.push('expected null, got ' + (got && got.status)); return why; }
    if (got === null) return ['got null'];
    if (got.status !== exp.status) why.push('status ' + got.status);
    if ((got.reason || null) !== (exp.reason || null)) why.push('reason ' + got.reason);
    if (!sameSet(got.warnings, exp.warnings)) why.push('warnings ' + JSON.stringify(got.warnings));
    fieldsMatch(exp.fields, got.fields, why);
    if (!sameSet(got.missing, exp.missing)) why.push('missing ' + JSON.stringify(got.missing));
    if ((got.selected || null) !== (exp.selected || null)) why.push('selected ' + got.selected);
    // r3: conflicts is part of the return contract (spec v0.5 §2); every expectation must state it and it is always compared.
    if (!Array.isArray(exp.conflicts)) why.push('expectation has no conflicts list');
    else if (!Array.isArray(got.conflicts) || !sameSet(got.conflicts, exp.conflicts)) why.push('conflicts ' + JSON.stringify(got.conflicts));
    if (got.ruleVersion !== versions.ruleVersion) why.push('ruleVersion ' + got.ruleVersion);
    if (got.templateTableVersion !== versions.templateTableVersion) why.push('templateTableVersion ' + got.templateTableVersion);
    const full = c.candidatesFull || [];
    if (got.candidates.length !== full.length) why.push('candidates ' + got.candidates.length + ' != ' + full.length);
    full.forEach((e, i) => {
      const g = got.candidates[i]; if (!g) return;
      if (g.closed !== e.closed) why.push('c' + (i + 1) + ' closed');
      if (!sameSet(g.keySet, e.keySet)) why.push('c' + (i + 1) + ' keySet ' + JSON.stringify(g.keySet));
      if (!sameMap(g.placeholder, e.placeholder)) why.push('c' + (i + 1) + ' placeholder ' + JSON.stringify(g.placeholder));
      if (g.verdict !== e.verdict) why.push('c' + (i + 1) + ' verdict ' + g.verdict);
    });
    return why.concat(boundaryWhy(c.input, c.tag, got.candidates));
  }

  function positionWhy(p, got) {
    if (!got) return ['got null'];
    const why = [];
    if (got.candidates.length !== p.expected.length) why.push('candidate count');
    p.expected.forEach((e, i) => { const g = got.candidates[i] || {};
      for (const k of ['id', 'start', 'end', 'closed']) if (g[k] !== e[k]) why.push(e.id + ' ' + k + ' ' + g[k] + ' != ' + e[k]); });
    return why;
  }

  // Runs the whole fixed suite against one ParseV2 implementation; returns counts and failures.
  function runSuite(P, spec) {
    const fails = [];
    for (const c of spec.cases) {
      let why; try { why = judge(c, P.parseBlockV2(c.input, c.tag, spec.keysets[c.keys]), spec.expectedVersions); } catch (e) { why = ['ERROR ' + e]; }
      if (why.length) fails.push({ id: c.id, why });
    }
    for (const p of spec.positionCases) {
      let why; try { why = positionWhy(p, P.parseBlockV2(p.input, p.tag, spec.keysets[p.keys])); } catch (e) { why = ['ERROR ' + e]; }
      if (why.length) fails.push({ id: p.id, why });
    }
    for (const e of spec.eligibleCases) {
      let g; try { g = P.eligible(e.result, e.opts); } catch (x) { g = 'ERROR ' + x; }
      if (g !== e.expected) fails.push({ id: e.id, why: ['eligible ' + g] });
    }
    const total = spec.cases.length + spec.positionCases.length + spec.eligibleCases.length;
    return { total, pass: total - fails.length, fails };
  }

  global.HarnessCommon = { judge, positionWhy, boundaryWhy, runSuite };
})(globalThis);
