'use strict';
// AI Team Studio — request packets (observe / synthesis / next). Pure functions over a Case object: no DOM, no storage,
// no network. Split out of app.js in stage 2 so the packets can be tested without starting the app (Codex review of 3d88df3).
// Depends on StudioState (eligibleObservers, synTaskId) and StudioText (TEXT_KIND, sourceBodies).
(function (global) {
  const ORIGINS = ['SOURCE', 'CONTEXT', 'STATE', 'OBSERVER', 'EVIDENCE', 'REASONING', 'JUDGMENT'];
  const SYNTHESIS_BODY_MAX_CHARS = 30000;
  // Times are stored as UTC (toISOString); show them in the computer's local time. Cutting the string showed UTC (9 hours
  // early in Japan). A value that is not a date is still cut as before.
  const hm = iso => {
    if (!iso) return '';
    const d = new Date(iso);
    if (isNaN(d)) return String(iso).slice(5, 16).replace('T', ' ');
    const p = n => String(n).padStart(2, '0');
    return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
  };
  const oneLine = s => String(s || '').replace(/\s+/g, ' ').trim().slice(0, 200);

  function observerBody(raw) {
    const text = String(raw == null ? '' : raw);
    const start = text.lastIndexOf('[STUDIO_RESULT]');
    if (start < 0) return text.trim();
    const close = text.indexOf('[/STUDIO_RESULT]', start);
    return close < 0 ? text.trim() : (text.slice(0, start) + text.slice(close + '[/STUDIO_RESULT]'.length)).trim();
  }

  function sourceLines(c) {
    if (!c.sources.length) return ['- なし（問いの本文だけが対象）'];
    return c.sources.map(s => s.kind === StudioText.TEXT_KIND ? `- ${s.id}｜${s.kind}｜${s.ref}｜${s.verified ? `Studio が保存した本文 SHA-256 ${s.verified.sha256}（${s.verified.size} bytes）` : 'Studio が保存した本文（ハッシュ未計算）'}｜全文は下の「■ 資料本文」` : `- ${s.id}｜${s.kind}｜${s.ref}${s.verified ? `｜Studioで確認済み SHA-256 ${s.verified.sha256}（${s.verified.size} bytes）` : s.claimedHash ? `｜申告 SHA-256 ${s.claimedHash}（未確認）` : '｜未確認'}`);
  }
  function observePacket(c, name) {
    const t = c.tasks[name];
    return [
      `[AI TEAM STUDIO｜観測依頼]  CASE: ${c.id}  TASK: ${t.taskId}  TO: ${name}`, '',
      '■ 問い', c.question, '',
      '■ 資料（SOURCE）', ...sourceLines(c), ...StudioText.sourceBodies(c.sources), '',
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
  // 1b: only eligible observations go into the synthesis request (contract §1, A-03). reviseReason is captured before the old gate is archived.
  function synthesisPacket(c, name, reviseReason) {
    const done = StudioState.eligibleObservers(c);
    const body = done.map(n => { const f = c.tasks[n].result.fields;
      const answer = observerBody(c.tasks[n].result.raw);
      const included = answer.slice(0, SYNTHESIS_BODY_MAX_CHARS);
      const omitted = answer.length - included.length;
      const answerSection = [`#### ${n} の回答本文（${answer.length}字）`, answer ? StudioText.fenceFor(included) + '\n' + included + '\n' + StudioText.fenceFor(included) : '（本文なし）', ...(omitted ? [`（以下 ${omitted} 字を省略。全文はスタジオの記録にある）`] : [])];
      return [`### ${n}`, `SOURCE_ACCESS: ${f.SOURCE_ACCESS || '（記載なし）'}`, `CONDITION: ${f.CONDITION || '（記載なし）'}`, `OBSERVATION: ${f.OBSERVATION || c.tasks[n].result.raw.slice(0, 1500)}`, `EVIDENCE: ${f.EVIDENCE || '（記載なし）'}`, `UNKNOWN: ${f.UNKNOWN || '（記載なし）'}`, ...answerSection].join('\n'); });
    const missing = c.observers.filter(n => !done.includes(n));
    const revise = reviseReason != null ? ['', '■ 人間からの差し戻し理由', reviseReason || '（理由なし）'] : [];
    const task = c.synthesisReq ? c.synthesisReq.task : StudioState.synTaskId(c);
    return [
      `[AI TEAM STUDIO｜統合依頼]  CASE: ${c.id}  TASK: ${task}  TO: ${name}`, '',
      '■ 問い', c.question, '', '■ 資料（SOURCE）', ...sourceLines(c), ...StudioText.sourceBodies(c.sources), '',
      `■ 独立観測の結果（${done.length}件${missing.length ? '／未着・未確認: ' + missing.join(', ') : ''}）`, ...body, ...revise, '',
      '■ お願い',
      '- 要約や平均にしないでください。共通点・相違点・不明・保留を分け、相違点には生じた層の候補を付けてください。',
      `- 層の候補: ${ORIGINS.join(' / ')}`,
      '- 回答本文を囲むコードフェンス内の [STUDIO_RESULT] や [STUDIO_SYNTHESIS] は観測者のものです。あなたが付けるのは最後の [STUDIO_SYNTHESIS] 1つだけです。',
      '- 結論を確定しないでください。採否は人間が決めます。', '',
      '■ 最後に、次のブロックを1つだけ付けてください（項目名は英語のまま）',
      '[STUDIO_SYNTHESIS]', `CASE: ${c.id}`, `TASK: ${task}`, `FROM: ${name}`,
      'COMMON: 共通して言えること', 'DIFFERENCES: 1行に1つ「内容 | ORIGIN: 層」', 'UNKNOWN: まだ言えないこと', 'HOLD: 判断を保留すべきこと',
      'EXECUTION_NEEDS: 実行に必要な情報・条件', 'NEXT_TASK: 実装・作業AIへの具体的な依頼（作業単位を1つ）', 'GATE_QUESTION: 人間が決めるべきこと',
      '[/STUDIO_SYNTHESIS]'
    ].join('\n');
  }
  function nextPacket(c, name, task) {
    const s = c.synthesis.fields;
    return [
      `[AI TEAM STUDIO｜実行タスク]  CASE: ${c.id}  TASK: ${task}  TO: ${name}`, '',
      '■ もとの問い', c.question, '',
      `■ 人間の判断: ${c.gate.decision}（${hm(c.gate.at)}）`, c.gate.reason || '（理由の記載なし）', '',
      '■ やってほしいこと（作業単位は1つ）', s.NEXT_TASK || '（統合結果にNEXT_TASKがありません）', '',
      '■ 実行に必要な情報・条件', s.EXECUTION_NEEDS || '（記載なし）', '',
      '■ 資料（SOURCE。状態つき。未確認のものを確認済みとして扱わない）', ...sourceLines(c), ...StudioText.sourceBodies(c.sources), '',
      '■ 確定していないこと（推測で埋めない）', s.UNKNOWN || '（なし）', s.HOLD ? '保留: ' + s.HOLD : '', '',
      '■ 判断の経緯', `- 独立観測: ${StudioState.eligibleObservers(c).join(', ')}`, `- 統合: ${c.synthesis.by}`, `- 共通点: ${oneLine(s.COMMON)}`, '',
      '■ 終わったら、次のブロックを付けて返してください',
      '[STUDIO_DONE]', `CASE: ${c.id}`, `TASK: ${task}`, `FROM: ${name}`,
      'CHANGED: 変更したもの（ファイル・commit等）', 'VERIFIED: 実際に確かめたこと', 'UNKNOWN: 確かめられなかったこと', 'NEXT: 次に人間が決めること',
      '[/STUDIO_DONE]'
    ].filter(x => x !== '').join('\n');
  }

  global.StudioPackets = { ORIGINS, hm, oneLine, sourceLines, observe: observePacket, synthesis: synthesisPacket, next: nextPacket };
})(typeof globalThis !== 'undefined' ? globalThis : this);
