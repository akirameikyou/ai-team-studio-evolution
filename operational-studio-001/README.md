# AI Team Studio — 実運用版 001

問いを1つ書くと、Studio が SOURCE の整理、各AIへの観測依頼、結果の回収、差分の検出、統合依頼、人間の判断（GATE）、次の実行タスクまでを運びます。人間が行うのは、問いを書くことと判断することです。

7段階（CASE → SOURCE → OBSERVE → DIFFERENCE → SYNTHESIS → HUMAN GATE → NEXT）はそのまま残しています。ただし人間が段階ごとに入力する必要はありません。画面上部の「次にすること」に、その時点で押すボタンが1つか2つ出るだけです。

## 開き方

ビルドは不要です。Web Crypto（ハッシュ計算）を使うため、`file://` ではなく localhost で開いてください。

```text
cd operational-studio-001
python -m http.server 8773 --bind 127.0.0.1
```

ブラウザで `http://127.0.0.1:8773/` を開きます。

## AIとのつなぎ方（コネクタ）

AIごとに、右上の「AIの接続設定」で選びます。

| コネクタ | 動き | 人間の手間 |
|---|---|---|
| 手動（既定） | 依頼文をクリップボードにコピーする。人間がそのAIに貼り、回答を Studio に貼り戻す | コピー1・貼り付け2 |
| ローカル中継 / agent | 中継が `proxy/exchange/<CASE>/<TASK>/to-<AI>.md` を書く。Claude Code や Codex がそれを読み、`from-<AI>.md` を書く。Studio は8秒ごとに自動で回収する | なし（AIにフォルダを見るよう一度伝えるだけ） |
| ローカル中継 / api:openai・anthropic・gemini | 中継がサーバー側でAPIを呼び、回答を返す | なし（**未検証**。下記参照） |
| ローカル中継 / mock | 内容のない模擬応答を返す。運搬経路のテスト専用 | なし |

ブラウザは中継（127.0.0.1）にしか送信しません。外部への送信は中継が行います。

### ローカル中継の起動

```text
cd operational-studio-001/proxy
python studio_proxy.py            # http://127.0.0.1:8787
```

必要なのは Python 3 の標準ライブラリだけです。起動したら、Studio の「AIの接続設定」で中継URLに `http://127.0.0.1:8787` を入れ、「接続を確認」を押します。

## APIキーの扱い（重要）

- APIキーは **HTML・JS・GitHub には一切書きません**。ブラウザ側にはキーを入れる欄がありません。
- キーは `proxy/.env`（`.gitignore` 済み）または環境変数に置きます。書式は `proxy/.env.example` にあります。
- 中継は `127.0.0.1` のみで待ち受けます。CORS で許可しているのは localhost と `https://akirameikyou.github.io` だけです。
- `proxy/exchange/`（AIとのやりとりの実ファイル）も `.gitignore` 済みです。

## 記録

- 保存先はこのブラウザ（localStorage）です。保存データが読めないときは保存を止め、上書きを防ぎます。画面上部に警告が出ます。
- 「記録をJSONで保存」で書き出し、「JSONを読み込む」で復元できます。読めない保存データからの復旧にも使えます。
- 「Markdownで保存」で、問い・SOURCE・各観測・差分・統合・判断・次タスクを1つの文書にまとめます。

制約と未実装の項目は [LIMITATIONS.md](LIMITATIONS.md)、実際の試験記録は [TEST_REPORT.md](TEST_REPORT.md) にあります。
