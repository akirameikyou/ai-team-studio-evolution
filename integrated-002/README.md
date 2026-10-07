# AI Team Studio Integrated 002

実使用を目的にした空のCaseベースPrototype。

7段階:
CASE → SOURCE → OBSERVE/LENS → DIFFERENCE → SYNTHESIS → HUMAN GATE → NEXT

目的は「人間の簡単な問いを、複数AIが読める共有Caseに変換し、観測結果を回収し、次の実行Taskへ渡せるか」を検証すること。

AI APIは未接続。まず作業状態とCase Packetを検証する。

## 起動

`index.html` をブラウザで開く。またはこのフォルダで `python -m http.server 8000 --bind 127.0.0.1` を実行し、`http://127.0.0.1:8000/` を開く。

## この公開版について

- 5者のコンペ提出物（canonical ZIP）と SOURCE_INDEX / SOURCE_MANIFEST は、この公開版には含めていない。CASE-001 のSOURCEは、利用者が自分の環境で登録する。
