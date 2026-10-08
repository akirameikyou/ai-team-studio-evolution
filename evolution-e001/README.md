# AI Team Studio Evolution — E001

Accepted Specification E002 に基づく E001 の実装場所です。既存の Studio（Lite、Operational 001、Integrated 002、Prototype 001）からは独立しています。既存のコードとデータには触れません。

**現在の段階：E001-D（実行の文脈の記録）。** E001-B で Schema と検証器、E001-C で受け取ったバイト列をそのまま保存する層、E001-D で Case / Trial / Run / 凍結した Run Manifest / Transfer の記録と出所の追跡を作りました。画面やサーバーはまだありません。

## E001 の制約

- **GitHub Pages 単体では動作しません。** 正本の保存先がファイル（file-backed JSON）であり、ブラウザだけでは書き込めないためです。
- **スマートフォン単体は E001 の対象外です。**
- **PC 上でローカルの Python サーバーが必要です**（Python 3.12、標準ライブラリのみ。pip install 不要）。サーバーと画面は E001-G 以降で作ります。
- **手動経路（Manual Provider）の独立性は、最大でも `PACKET_CONTROLLED` です。** Studio は各 AI に渡す依頼文（packet）の中身を統制・記録できますが、人間が外部の AI 画面で実際に何をしたか、AI サービス側の記憶や履歴がどう働いたかは確認できません。確認できないことは `UNKNOWN` と記録します。手動経路で `ENFORCED` を表示することはありません。
- **OS・ファイルシステム水準の改ざん耐性は保証しません。** E001 が保証するのは、アプリケーション経由での書き込み一回性・ID 重複の拒否・正確なバイト列の SHA-256・追記のみの Event です。ファイルを直接書き換えられた場合の防止はしません（検出はハッシュ照合の範囲で行います）。
- API・中継（Proxy）・Local LLM・シェル実行・Git 操作・外部公開・移行は E001 では実装しません。

## 構成

```
evolution-e001/
  domain/schema/        正式 E001 Schema と検証器（E001-B）
  application/          実行の文脈（Case・Trial・Run・Manifest・Transfer）と出所の追跡（E001-D）
  infrastructure/storage/  Artifact / Event（E001-C）とレコード（E001-D）のファイル保存
  tests/unit/           単体テスト（Schema・文書）
  tests/persistence/    保存層のテスト（書き込み一回性・破損検出・Event 接続・書き出し/取り込み・レコード）
  tests/integration/    実行の文脈のテスト（凍結・出所の追跡・P001 / P002）
  tests/fixtures/       非機密のテストデータ（Git 管理。SECRET-A/B/C を含む）
  docs/                 仕様書（無改変）・旧 Schema（参考）・SHA256SUMS・BASELINE・TRACEABILITY・検証範囲
  data/                 実際の CASE データ（Git 管理外）
```

`ui/` は後のフェーズで作ります。

## 文書

| 文書 | 内容 |
|---|---|
| [docs/AI_Team_Studio_Accepted_Specification_E002.md](docs/AI_Team_Studio_Accepted_Specification_E002.md) | 実装の正本（無改変） |
| [docs/AI_Team_Studio_Claude_Code_Implementation_Request_E001.md](docs/AI_Team_Studio_Claude_Code_Implementation_Request_E001.md) | 実装依頼（無改変） |
| [docs/reference/AI_Team_Studio_STUDIO_SCHEMA_E001.json](docs/reference/AI_Team_Studio_STUDIO_SCHEMA_E001.json) | 旧 Schema（参考資料・無改変。正式 Schema ではない） |
| [docs/SHA256SUMS.txt](docs/SHA256SUMS.txt) | 上の3文書の SHA-256 |
| [docs/BASELINE_E001-A.md](docs/BASELINE_E001-A.md) | 着手時点のリポジトリと環境 |
| [docs/TRACEABILITY.md](docs/TRACEABILITY.md) | E002 → Schema → 実装 → テストの対応と、設計判断 |
| [docs/SCHEMA_VALIDATION_SCOPE.md](docs/SCHEMA_VALIDATION_SCOPE.md) | 検証器が検証するもの・しないもの |
| [docs/STORAGE_E001-C.md](docs/STORAGE_E001-C.md) | 保存の構成、書き込み一回性、ハッシュ、検出できる破損、保証しないこと |
| [docs/EXECUTION_CONTEXT_E001-D.md](docs/EXECUTION_CONTEXT_E001-D.md) | 実行の文脈、Manifest の凍結、出所の追跡、`NODE-studio`、既知の制約 |

## テスト

```
cd evolution-e001
python -m unittest discover -s tests -t .
```

文書のハッシュ確認（Git Bash など）：

```
cd evolution-e001/docs
sha256sum -c SHA256SUMS.txt
```

fixture の再生成（中身が変わるとテストが失敗します）：

```
cd evolution-e001
python -m tests.fixtures.make_fixtures
```
