'use strict';
// Stage 1d-A tests: SOURCE extraction from text (1d-1) and splitting the synthesis DIFFERENCES for display (1d-2).
// run1d(T) runs every test against a StudioText-like object. Pure functions only (no DOM), so it also runs in Node.
function run1d(T) {

const results = []; let cur = null;
const check = (cond, msg) => results.push({ test: cur, ok: !!cond, msg });
const test = (name, fn) => { cur = name; try { fn(); } catch (e) { results.push({ test: name, ok: false, msg: 'ERROR ' + e.stack }); } };
const refs = list => list.map(s => s.kind + ':' + s.ref).join(' | ');
const Q = { bareNames: false };   // text of the question (automatic extraction)
const M = { bareNames: true };    // what a person typed into "add SOURCE" (explicit registration)

test('X-01 問いの中のファイル名だけの記述は取り出さない（1d-1）', () => {
  check(T.extractSources('観測では from-Codex.md に結果を書いてください', Q).length === 0, 'from-Codex.md は資料にならない（10/14 の実例）');
  check(T.extractSources('parse_v2.js と README.md と data.csv を見て', Q).length === 0, '一般的なファイル名も資料にならない');
  check(T.extractSources('README と Makefile を確認', Q).length === 0, '拡張子のない名前は、どちらの経路でも資料にならない');
});

test('X-02 URL は取り出す（末尾のファイル名を別の資料にしない）', () => {
  for (const o of [Q, M]) {
    const r = T.extractSources('参考：https://example.com/a/b/file.md 。続き', o);
    check(r.length === 1 && r[0].kind === 'URL' && r[0].ref === 'https://example.com/a/b/file.md', 'URL を1件だけ（末尾の。を除く）' + (o.bareNames ? '・追加欄' : '・問い') + ' ' + refs(r));
  }
  const g = T.extractSources('https://raw.githubusercontent.com/o/r/abc/x.html を読む', Q);
  check(g.length === 1 && g[0].kind === 'GitHub', 'GitHub の URL は種類 GitHub ' + refs(g));
});

test('X-03 絶対パスは取り出す', () => {
  for (const o of [Q, M]) {
    const r = T.extractSources('資料は C:\\Users\\x\\doc.pdf です', o);
    check(r.length === 1 && r[0].kind === 'ファイル' && r[0].ref === 'C:\\Users\\x\\doc.pdf', 'Windows の絶対パスを1件 ' + refs(r));
  }
});

test('X-04 資料を追加の欄では、ファイル名も登録する（従来どおり）', () => {
  const r = T.extractSources('from-Codex.md', M);
  check(r.length === 1 && r[0].kind === 'ファイル' && r[0].ref === 'from-Codex.md', 'ファイル名を1件 ' + refs(r));
  check(T.extractSources('a.md と a.md', M).length === 1, '同じ名前は1件だけ');
  const p = T.extractSources('C:\\w\\report.pdf と report.pdf', M);
  check(p.length === 1 && p[0].ref === 'C:\\w\\report.pdf', '絶対パスに含まれるファイル名を二重に登録しない ' + refs(p));
  check(T.extractSources('README', M).length === 0, '拡張子のない名前は登録しない');
});

test('X-05 番号・申告ハッシュ・既定値', () => {
  const h = 'A'.repeat(64);
  const r = T.extractSources('https://example.com/x と C:\\d\\y.zip、ハッシュ ' + h, Q);
  check(r.map(s => s.id).join() === 'S1,S2' && r[0].claimedHash === h.toLowerCase() && r[1].claimedHash === null, '出現順に S1・S2、申告ハッシュは最初の資料に小文字で付く');
  check(T.extractSources('ハッシュだけ ' + h, Q).length === 0, '資料がなければハッシュだけでは何も作らない');
  check(r.every(s => s.verified === null && JSON.stringify(s.access) === '{}'), '確認前の既定値');
  const d = T.extractSources('from-Codex.md');
  check(d.length === 1, '引数を省くと従来どおり（ファイル名も取り出す）');
});

test('X-06 URL の直後に空白なしで日本語が続いても、URL に含めない（画面確認で発見し、期待の固定後に追加）', () => {
  const cases = [['参考 https://example.com/spec.md（試験データ）', 'https://example.com/spec.md'], ['https://example.com/a、次を見る', 'https://example.com/a'],
    ['「https://example.com/b」を参照', 'https://example.com/b'], ['https://example.com/c。続き', 'https://example.com/c'], ['URLはhttps://example.com/d?x=1&y=2です', 'https://example.com/d?x=1&y=2']];
  for (const [text, want] of cases) { const r = T.extractSources(text, Q); check(r.length === 1 && r[0].ref === want, text + ' → ' + refs(r)); }
});

const ORIGIN_REAL = '具体例として、GPTはジョン万次郎の漂流・鳥島・アメリカへの渡航を、Geminiは関ヶ原の戦い・徳川家康と石田三成ら・関ヶ原の地形と街道を選択した | ORIGIN: OBSERVER DIFFERENCES: GPTは遭難から異文化との接触に至る経緯を、Geminiは地形と布陣・行動決定の関係を重視した | ORIGIN: REASONING DIFFERENCES: GPTは事前知識あり・Web利用可能だが未使用と記録し、Geminiは一般知識と論理推論に基づく回答・Webアクセス不可と記録した | ORIGIN: CONTEXT DIFFERENCES: GPTは具体例を既知の歴史知識に基づく推論として説明し、Geminiは関ヶ原の史実をREPORTEDとして分類した | ORIGIN: EVIDENCE';
const view = list => list.map(d => '[' + d.origin + (d.auto ? '*' : '') + '] ' + d.text).join(' / ');

test('D-01 改行で分かれた返事は従来どおり', () => {
  const r = T.splitDifferences('A の違い | ORIGIN: SOURCE\nB の違い | ORIGIN: OBSERVER');
  check(r.length === 2 && r[0].text === 'A の違い' && r[0].origin === 'SOURCE' && r[1].origin === 'OBSERVER' && r.every(d => !d.auto), '2件、自動分割の印なし ' + view(r));
  const b = T.splitDifferences('- A | ORIGIN: SOURCE\n- B | ORIGIN: CONTEXT');
  check(b.length === 2 && b[0].text === 'A' && b[1].text === 'B' && b.every(d => !d.auto), '箇条書きも2件 ' + view(b));
});

test('D-02 改行が失われた実例（T-LOOP 002）を4件に分ける', () => {
  const r = T.splitDifferences(ORIGIN_REAL);
  check(r.length === 4, '4件 ' + r.length);
  check(r.map(d => d.origin).join() === 'OBSERVER,REASONING,CONTEXT,EVIDENCE', '層の順序');
  check(r[0].text.startsWith('具体例として') && r[1].text.startsWith('GPTは遭難から') && r[2].text.startsWith('GPTは事前知識あり') && r[3].text.startsWith('GPTは具体例を既知'), '各項目の先頭');
  check(r.every(d => !/DIFFERENCES\s*:/i.test(d.text) && !/ORIGIN\s*:/i.test(d.text)), '項目の本文に「DIFFERENCES:」「ORIGIN:」が残らない');
  check(r.every(d => d.auto), 'すべて「自動分割（改行なし）」の印つき');
});

test('D-03 確実に分けられないときは、原文を残して未分類', () => {
  const n = T.splitDifferences('比較可能な複数の観測がないため、相違点は特定できない。');
  check(n.length === 1 && n[0].origin === '未分類' && n[0].text === '比較可能な複数の観測がないため、相違点は特定できない。' && !n[0].auto, 'ORIGIN がなければ1件・未分類・原文そのまま');
  const u = T.splitDifferences('A の違い | ORIGIN: FOO B の違い | ORIGIN: SOURCE');
  check(u.length === 1 && u[0].origin === '未分類' && u[0].text === 'A の違い | ORIGIN: FOO B の違い | ORIGIN: SOURCE' && !u[0].auto, '1行に知らない層があれば自動分割せず、原文を1件で残す ' + view(u));
  const p = T.splitDifferences('ORIGIN という語を含む文章 | 層は不明');
  check(p.length === 1 && p[0].origin === '未分類' && p[0].text === 'ORIGIN という語を含む文章 | 層は不明', '「| ORIGIN: 層」の形でない文には反応しない');
});

test('D-04 内容を黙って消さない', () => {
  const t = T.splitDifferences('A | ORIGIN: SOURCE B | ORIGIN: STATE 補足の説明');
  check(t.length === 3 && t[2].text === '補足の説明' && t[2].origin === '未分類', '最後の層の後ろの文は、未分類の項目として残す ' + view(t));
  const s = T.splitDifferences('A の違い | ORIGIN: SOURCE 補足');
  check(s.length === 2 && s[0].text === 'A の違い' && s[0].origin === 'SOURCE' && s[1].text === '補足' && s[1].origin === '未分類', '1件の行でも、層の後ろの文を残す（従来は表示から落ちていた）' + view(s));
  const e = T.splitDifferences('| ORIGIN: SOURCE | ORIGIN: STATE');
  check(e.length === 2 && e.every(d => d.text === '（内容なし）') && e[0].origin === 'SOURCE' && e[1].origin === 'STATE', '空の項目も「（内容なし）」として残す ' + view(e));
});

test('D-05 そのほか', () => {
  check(T.splitDifferences('').length === 0 && T.splitDifferences(undefined).length === 0 && T.splitDifferences(null).length === 0, '空・未定義は0件');
  const c = T.splitDifferences('A | origin: observer');
  check(c.length === 1 && c[0].origin === 'OBSERVER' && c[0].text === 'A', '大文字小文字を区別しない');
  const k = T.splitDifferences('A | ORIGIN: Foo');
  check(k.length === 1 && k[0].origin === 'FOO' && k[0].text === 'A', '1件だけの行の層は、知らない名前でも従来どおり表示する');
  const m = T.splitDifferences('X | ORIGIN: SOURCE\n' + ORIGIN_REAL);
  check(m.length === 5 && !m[0].auto && m.slice(1).every(d => d.auto), '改行ありと改行なしの混在：改行で切った項目には印なし、1行の中で切った項目には印 ' + m.length);
});

test('X-07 URL は ASCII の括弧でも切る（Codex C48 で追加）', () => {
  for (const [text, want] of [['参考 https://example.com/a(test) を見る', 'https://example.com/a'], ['(https://example.com/b) を見る', 'https://example.com/b']]) {
    const r = T.extractSources(text, Q); check(r.length === 1 && r[0].ref === want, text + ' → ' + refs(r));
  }
});

test('D-06 層名は語の全体で読む。既知の層と完全一致しなければ未知（Codex C48 で追加）', () => {
  const a = T.splitDifferences('A | ORIGIN: SOURCE2 B | ORIGIN: STATE');
  check(a.length === 1 && a[0].origin === '未分類' && a[0].text === 'A | ORIGIN: SOURCE2 B | ORIGIN: STATE' && !a[0].auto, 'SOURCE2 は未知の層。1行を分けず原文のまま ' + view(a));
  const b = T.splitDifferences('A | ORIGIN: SOURCE-X B | ORIGIN: STATE');
  check(b.length === 1 && b[0].origin === '未分類' && !b[0].auto, 'SOURCE-X も未知の層 ' + view(b));
  const c = T.splitDifferences('A の違い | ORIGIN: SOURCE2');
  check(c.length === 1 && c[0].origin === 'SOURCE2' && c[0].text === 'A の違い', '印が1つだけの行は分けないので、層名は書かれたとおり（SOURCE と読み替えない）' + view(c));
  const d = T.splitDifferences('A | ORIGIN: SOURCE。B | ORIGIN: STATE');
  check(d.length === 2 && d[0].origin === 'SOURCE' && d[1].origin === 'STATE' && d.every(x => x.auto) && d[1].text === '。B', '層名の直後の句読点は層名に含めない ' + view(d));
});

test('R-01 中継のファイル名の変換は、中継（Python の safe）と同じ（1d-3）', () => {
  const cases = [['Claude Code', 'Claude_Code'], ['Codex', 'Codex'], ['GPT-6 β', 'GPT-6_β'], ['クロード コード', 'クロード_コード'],
    ['a/b\\c', 'a_b_c'], ['  lead', '_lead'], ['x'.repeat(100), 'x'.repeat(80)], ['CASE-20261015-001-OBS-ClaudeCode', 'CASE-20261015-001-OBS-ClaudeCode']];
  for (const [n, want] of cases) check(T.relaySafeName(n) === want, JSON.stringify(n) + ' → ' + JSON.stringify(T.relaySafeName(n)));
  check(T.relaySafeName('..') === null && T.relaySafeName('.') === null && T.relaySafeName('') === null, '中継が拒否する名前（空・.・..）は null');
});

test('R-02 依頼ファイルと結果ファイルのパス（1d-3）', () => {
  const w = T.relayPaths('C:\\ex\\exchange', 'CASE-1', 'CASE-1-OBS-ClaudeCode', 'Claude Code');
  check(w && w.toFile === 'C:\\ex\\exchange\\CASE-1\\CASE-1-OBS-ClaudeCode\\to-Claude_Code.md' && w.fromFile === 'C:\\ex\\exchange\\CASE-1\\CASE-1-OBS-ClaudeCode\\from-Claude_Code.md' && !w.relative, 'Windows の受け渡しフォルダ ' + (w && w.toFile));
  const t = T.relayPaths('C:\\ex\\exchange\\', 'CASE-1', 'CASE-1-SYN', 'GPT');
  check(t && t.toFile === 'C:\\ex\\exchange\\CASE-1\\CASE-1-SYN\\to-GPT.md', '末尾の区切りは二重にしない ' + (t && t.toFile));
  const u = T.relayPaths('/home/u/exchange', 'CASE-1', 'CASE-1-NEXT', 'Codex');
  check(u && u.fromFile === '/home/u/exchange/CASE-1/CASE-1-NEXT/from-Codex.md', 'POSIX のパス ' + (u && u.fromFile));
  const r = T.relayPaths('', 'CASE-1', 'CASE-1-NEXT', 'Codex');
  check(r && r.relative && r.toFile === 'proxy/exchange/CASE-1/CASE-1-NEXT/to-Codex.md', '受け渡しフォルダが分からないときは、中継からの相対パス ' + (r && r.toFile));
  check(T.relayPaths('C:\\ex', 'CASE-1', 'CASE-1-NEXT', '..') === null, '名前が使えなければ null');
});

test('R-03 AI への指示文（1d-3）', () => {
  const p = T.relayPaths('C:\\ex', 'CASE-1', 'CASE-1-OBS-Codex', 'Codex');
  const s = T.agentInstruction(p);
  check(s.includes(p.toFile) && s.includes(p.fromFile), '依頼ファイルと結果ファイルの完全なパスを含む');
  check(s.includes('このファイルだけ') && s.includes('ほかのファイルは作成・変更・削除しないでください'), '書いてよいのは結果ファイルだけ、と明記');
  check(s.includes('TASK') && s.includes('止まって'), '依頼ファイルがない・TASK が合わないときは止まる、と明記');
  check(T.agentInstruction(null) === '', 'パスがなければ空');
});


const NL = String.fromCharCode(10);
test('T-01 本文の資料を作る（2-1A）', () => {
  check(T.textSource('S1', 'x', '   ' + NL + ' ', 'h', 1, 't').error, '空白だけの本文は登録しない');
  check(T.textSource('S1', 'x', 'a'.repeat(T.TEXT_MAX + 1), 'h', 1, 't').error, '上限を超える本文は登録しない');
  const body = ['本文', '', '二段落目'].join(NL);
  const s = T.textSource('S2', '  ', body, 'ab'.repeat(32), 20, '2026-10-21T00:00:00Z');
  check(!s.error && s.kind === T.TEXT_KIND && s.id === 'S2', '種類は本文・ID はそのまま');
  check(s.ref === '前提資料', '名前が空なら「前提資料」');
  check(s.text === body, '本文は書き換えない（空行も残す）');
  check(s.verified && s.verified.sha256 === 'ab'.repeat(32) && s.verified.size === 20, 'ハッシュと大きさを記録');
  check(T.textSource('S3', 'y', 'z', null, 1, 't').verified === null, 'ハッシュが無ければ未確認のまま');
});

test('T-02 依頼文に入れる本文の区切り（2-1A）', () => {
  check(T.sourceBodies([]).length === 0 && T.sourceBodies([{ id: 'S1', kind: 'URL', ref: 'https://x' }]).length === 0, '本文の資料が無ければ何も足さない');
  const s = T.textSource('S1', '前提', ['一行目', '', '三行目'].join(NL), 'cd'.repeat(32), 20, 't');
  check(T.textSource('S1', '前提', '本文', 'cd'.repeat(32), 99, 't').error, '渡された大きさが本文の UTF-8 バイト数と合わなければ登録しない');
  const b = T.sourceBodies([s]);
  check(b.includes(s.text), '本文は1つの要素のまま入る（空行を含んだまま）');
  check(b.filter(x => x !== '').includes(s.text), '空の要素を除く処理（実行タスクの依頼文）を通っても本文は残る');
  check(b.some(x => x.startsWith('--- S1「前提」 SHA-256 ' + 'cd'.repeat(32))) && b.includes('--- S1 ここまで ---'), '区切りに ID・名前・ハッシュ');
});

test('T-03 本文の二重登録（ハッシュがなくても本文で判定）', () => {
  const a = T.textSource('S1', 'a', '同じ本文', null, 1, 't'), b = T.textSource('S2', 'b', '同じ本文', null, 1, 't');
  check(T.isDuplicateText([a], b), 'ハッシュがないときも、同じ本文なら二重登録');
  check(!T.isDuplicateText([a], T.textSource('S2', 'b', '別の本文', null, 1, 't')), '本文が違えば登録できる');
  const h = 'ef'.repeat(32);
  check(T.isDuplicateText([T.textSource('S1', 'a', 'x', h, 1, 't')], T.textSource('S2', 'b', 'y', h, 1, 't')), 'ハッシュが同じなら二重登録');
  check(!T.isDuplicateText([{ id: 'S1', kind: 'URL', ref: '同じ本文', verified: null }], b), '本文以外の資料とは本文で比べない');
});

test('T-04 Markdown のコードの区切り', () => {
  const tick = '`';
  check(T.fenceFor('普通の本文') === tick.repeat(3), 'バッククォートがなければ3つ');
  check(T.fenceFor('前' + tick.repeat(3) + 'js 後') === tick.repeat(4), '本文に3つ続きがあれば4つ');
  check(T.fenceFor(tick.repeat(5)) === tick.repeat(6), '最長の続きより1つ長い');
});

test('T-05 絶対パスは全角の句読点・括弧で止まる（日本語のフォルダ名は残す）', () => {
  const bs = String.fromCharCode(92);
  const p1 = ['C:', 'GitHub', 'repo'].join(bs);
  const r1 = T.extractSources('リポジトリ ' + p1 + '）を、読み取り専用で', Q);
  check(r1.length === 1 && r1[0].ref === p1, '（ の手前で止まる ' + refs(r1));
  const p2 = ['C:', 'Users', 'x', 'ユニコ', 'a.md'].join(bs);
  const r2 = T.extractSources(p2 + '。続き', Q);
  check(r2.length === 1 && r2[0].ref === p2, '日本語のフォルダ名を含むパスは残し、。の手前で止まる ' + refs(r2));
  const r3 = T.extractSources('「' + p1 + '」と' + p2 + '、', Q);
  check(r3.length === 2 && r3[0].ref === p1 && r3[1].ref === p2, '」と、で区切られた2件 ' + refs(r3));
});

test('T-07 Case 番号（日付・連番・乱数）', () => {
  const d = new Date(2026, 9, 21, 9, 0, 0);
  const a = T.caseIdFor(d, [], 'Ab12CD');
  check(a === 'CASE-20261021-001-ab12cd', '日付・001・小文字の乱数 ' + a);
  check(T.caseIdFor(d, ['CASE-20261021-001', 'CASE-20261021-002-x1y2z3'], 'qwerty') === 'CASE-20261021-003-qwerty', '旧形式と新形式の両方の連番を避ける');
  check(T.caseIdFor(d, [], 'a-b') === null, '乱数が6文字に足りなければ作らない');
  const b = T.caseIdFor(d, [], 'zzzzzz');
  check(a.slice(0, 18) === b.slice(0, 18) && a !== b, '記録が消えて連番が 001 に戻っても、乱数の部分で別の番号になる');
  check(T.relaySafeName(a) === a, '中継のフォルダ名としてそのまま使える');
});

test('T-06 結果ファイルは一時ファイルに書いてから名前を変えるよう頼む', () => {
  const p = T.relayPaths('C:/x/exchange', 'C1', 'C1-OBS-Codex', 'Codex');
  const s = T.agentInstruction(p);
  check(s.includes('.tmp') && s.includes('書き終えてから'), '一時ファイルと名前の変更を依頼文に含む');
  check(s.includes('このファイルだけ'), '書いてよいのは結果ファイルだけ、の文はそのまま');
});

return results;
}
if (typeof module !== 'undefined') module.exports = { run1d };
