# data/

E001 の実際の CASE データ（Artifact・Event・Manifest・Snapshot・Export）の保存先です。

- このフォルダの中身は Git の管理外です（`evolution-e001/.gitignore`）。このファイルだけを追跡します。
- 理由：実際の問い・資料・AIの回答には、公開すべきでない内容が含まれうるためです。
- テスト用の非機密データはここに置きません。`tests/fixtures/` に置き、Git で追跡します。
- 保存形式と配置は E001-C（Artifact storage）以降で決まります。E001-B の時点では何も書き込みません。
