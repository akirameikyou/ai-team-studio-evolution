# BASELINE — E001-A（2026-10-08）

E001-A（監査と環境準備）の時点で確認した、リポジトリと実行環境の事実です。Claude Code が実物を確認しました。

## 1. Git

| 項目 | 内容 |
|---|---|
| リポジトリ | `akirameikyou/ai-team-studio-evolution` |
| E001 ブランチ | `feature/evolution-e001`（`main` @ `4554ec6f723e41ec7108a3582f98065f5846829d` から分岐。上流なし・未 push） |
| E001 作業領域 | `C:\GitHub\ai-team-studio-evolution-e001`（git worktree） |
| 元の作業ツリー | `C:\GitHub\ai-team-studio-evolution`（`feature/operational-studio-001` @ `8084dbb`）。worktree 作成の前後で状態不変を確認 |
| 元の作業ツリーの未確定物 | 変更中：`operational-studio-001/app.js`、`operational-studio-001/index.html`。未追跡：`experiments/EXP-01/RESULTS/`、`experiments/EXP-02/`（独立性のため意図的に未コミット） |
| `main` | `4554ec6`（origin と一致） |
| `feature/integrated-002` | `1e68d22`（origin と一致） |
| Lite（別リポジトリ） | `main` @ `a6cc25f`、origin より3コミット先行（未 push）、`relay-gate-check.html` 未追跡 |

**変更禁止（Human Gate 承認条件）：** main、feature/integrated-002、feature/operational-studio-001、Lite リポジトリ、未追跡の EXP-01 / EXP-02 結果、既存の Operational Studio。

## 2. 実行環境

| 項目 | 結果 |
|---|---|
| Python | 3.12.10（win32） |
| `open(..., 'xb')` | 同名の2回目の作成を `FileExistsError` で拒否 |
| `os.replace` | 動作（原子的な置き換え） |
| SHA-256 | CRLF を含むバイト列をそのまま計算 |
| ローカルサーバー | `127.0.0.1` で待ち受け可。`ThreadingHTTPServer`・`unittest`・`uuid` あり |
| 未導入 | Node.js、`jsonschema`。どちらも導入しない（pip install 禁止） |
| Git の改行設定 | このリポジトリは `core.autocrlf=false`。既存ファイルは LF |

## 3. 既存の記録形式（移行しない。記録のみ）

| 資産 | 記録先と形式 |
|---|---|
| Lite | localStorage `ats_trail_v1`（schemaVersion 2）、`ats_archive_v1`（最大20件）、`ats_outbound_v1`。JSON 書き出しは `buildExchange()`（形式名の定数値は未確認） |
| Operational 001 | localStorage `ats-operational-001`（version 1）。書き出し `format:'ats-operational-case'` v1。受け渡し `proxy/exchange/*.md`（Git 管理外） |
| Integrated 002 | localStorage `ai-team-studio-integrated-002…`。版番号なし |
| Prototype 001 | localStorage `atse-p001` |
| 他 AI 向けパッケージ | `C:\GitHub\ai-team-studio-exports\`（Git 管理外）、`ai-team-studio-source-package` v1 |

既存の Studio には、ファイルを正本とする保存の仕組みも、Schema ファイルも、テストもありませんでした。

## 4. 仕様書

| 文書 | 取得元 | SHA-256 |
|---|---|---|
| Accepted Specification E002 | Desktop（Downloads の同名ファイルと同一ハッシュ） | `97a9bfaf8019c9a5c84a2e1bbfa4d72acb410995a42de8ea8525813236736a23` |
| Claude Code Implementation Request E001 | Desktop（同上） | `a6fe229f725e3a6c2f4b86c91f493d16b8e2d50bcb904875e247eb85fc763655` |
| STUDIO_SCHEMA_E001（旧版・参考資料） | Downloads（Desktop にはなかった） | `53b8b5a071bdb603718d9f946832cf0ea75cb731cf2494c9589f1d4a4d741590` |

旧 Schema が以前のレビューで読んだものと同一かどうかは確定していません（当時のハッシュの記録がないため）。
