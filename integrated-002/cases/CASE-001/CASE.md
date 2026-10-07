# CASE-001 — AI Team Studio Integrated 002 Practical Test

## Question
AI Team Studio Integrated 002を実際のAI共同作業に使ったとき、
人間の運搬作業を減らしながら、複数AIの独立観測と最終実行を成立させられるか。

## Initial task
人間の簡単な質問をCaseとして置き、Studio上でAI別Taskに変換する。
各AIは自分のTaskとSOURCEを読み、Observation / Evidence / Unknownを返す。
その結果をCaseへ戻し、SynthesisからClaude/Codex等が実行できるTaskを作る。

## Success conditions
- 7段階が初見で理解できる
- SOURCEとObservation Conditionが残る
- AIごとの結果が混ざらない
- Differenceが見える
- UNKNOWNを結論に変えない
- Human Gateが意味を持つ
- 次の実装AIが必要情報を再収集せずに動ける
- 人間のcopy/pasteが減る

## Current status
OPEN / NOT YET OBSERVED

## Important
This Case is intentionally not pre-filled with AI answers.
