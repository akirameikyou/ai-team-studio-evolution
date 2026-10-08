# STORAGE — E001-C Artifact / RAW Evidence Storage

対象：`infrastructure/storage/`（`artifact_store.py`、`event_log.py`、`integrity.py`）

Artifact 層が保存するのは「AI が言ったこと」ではなく、**Studio が受け取ったバイト列そのもの**です。

```
RAW BYTES → Artifact → Event Evidence → Observation（E001-H）
```

## 1. ファイル構成

```
<store>/
  store.json                              形式・版の目印（作成時に1回だけ書く）
  artifacts/<artifactId>/payload.bin      受け取ったバイト列そのまま
  artifacts/<artifactId>/artifact.json    Schema の artifact レコード（contentLocation 形式）
  events/<sequence 10桁>.json             1 Event = 1 ファイル
```

- 実データの store は `evolution-e001/data/` の下に置きます（Git 管理外）。テストは一時フォルダだけを使います。
- 中身を JSON に埋め込まないので、読み直して書き戻す工程（再シリアライズ）でバイト列が変わることがありません。
- `store.json` のない空でないフォルダは、store として扱いません（既存のデータを取り込んで上書きしないため）。

## 2. 書き込み一回性（application-level write-once）

`ArtifactStore` の公開操作は `create` / `read` / `inspect` / `list_ids` / `verify` / `export_artifacts` / `import_artifacts` / `bundle_records` だけです。**更新・置き換え・削除の操作はありません**（テストで確認）。

`create` の手順：

1. 入力は bytes のみ（テキストは入口で1回だけ UTF-8 に変換する）。UTF-8 として読めないものは拒否します。
2. メタデータを組み立て、Schema で検証します。ここで失敗したら**何も書きません**。
3. `artifacts/<id>` を排他的に作成します。すでにあれば `DuplicateArtifactId`。ID はこの時点で予約され、二度と使われません。
4. `payload.bin` を書きます（一時ファイル → fsync → ハードリンク。既存のファイルがあればリンクが失敗するので、置き換えは起きません）。
5. `artifact.json` を同じ方法で書きます。**ここが確定の瞬間です。**
6. 書いたファイルを読み直して、ハッシュを照合します。
7. `ARTIFACT_RECEIVED` Event を追記します。

確定したファイルには OS の読み取り専用属性を付けます。これは事故防止のためで、改ざん防止ではありません。

## 3. ハッシュ

| 値 | 計算の対象 | 記録場所 |
|---|---|---|
| `contentHash`（SHA-256） | `payload.bin` の正確なバイト列。改行・BOM・空白を正規化しない | `artifact.json` と Event の `payload.artifactHash` |
| `metadataHash`（SHA-256） | `artifact.json` の正確なバイト列 | Event の `payload.metadataHash` |

`read` は毎回、両方のファイルを読み直して計算し直します。

- **artifactId とハッシュは別物です。** ID は識別子、ハッシュは中身が変わっていないことの証拠です。同じ中身を2回受け取れば、ID の違う2つの Artifact になります。
- **metadataHash は「メタデータの完全性を照合するための参照値」であり、Event Log 全体の改ざん検出の鎖（tamper-evidence chain）ではありません。** これで検証できるのは「payload と artifact.json が、Event に記録された時点の状態と一致しているか」です。**Event そのものが改ざんされていないことは保証しません。** Event 同士をハッシュでつなぐ鎖は E001-I 以降で扱います。

## 4. 確定と Event 記録は原子的ではない

Artifact の確定（手順5）と Event の追記（手順7）は別の書き込みです。その間で処理が止まると、**Event のない Artifact（ORPHAN）** が残ります。これを隠したり、自動で修復したりはしません。

| 状態 | 意味 | `read` | `verify` |
|---|---|---|---|
| OK | 中身・メタデータ・Event がそろって一致 | 返す（`event_linked=True`） | 問題なし |
| ORPHAN | 中身とメタデータは一致するが、Event がない | 返す（`event_linked=False`。呼び出し側に見える） | `ORPHAN` |
| INCOMPLETE | `artifact.json` がない（確定前に止まった） | 拒否 | `INCOMPLETE` |
| CORRUPT | それ以外の不一致 | 拒否（`ArtifactIntegrityError`） | 各コード |

ORPHAN は書き出し（export）の対象にしません。

## 5. contentLocation

`artifact.json` の `contentLocation` は、**読む場所の指示としては使いません。** 値は必ず `artifacts/<自分のID>/payload.bin` と一致しなければならず、違えば `BAD_LOCATION` として拒否します。中身は常に、そのArtifact自身のフォルダの `payload.bin` から読みます。したがって、パストラバーサル（`..`）、フォルダ外・絶対パス、存在しないファイル、**他の Artifact の payload**（ハッシュまで偽装した場合も含む）への参照は、どれも追跡されずに拒否されます。メタデータに `content`（中身の埋め込み）を書いた場合も拒否します。

## 6. 検出できる破損

| コード | 内容 |
|---|---|
| `HASH_MISMATCH` / `SIZE_MISMATCH` | payload.bin が変わった（1バイトの変更、改行の正規化を含む） |
| `METADATA_MISMATCH` | artifact.json が Event に記録された状態と違う（出所の書き換えなど） |
| `EVENT_HASH_MISMATCH` | contentHash が Event の記録と違う（payload とメタデータを辻褄を合わせて偽装した場合） |
| `INVALID_METADATA` / `ID_MISMATCH` / `BAD_LOCATION` / `MISSING_PAYLOAD` | メタデータの破損・ID の食い違い・参照先の不正・中身の欠落 |
| `UNEXPECTED_FILE` | 想定外のファイル（途中で残った一時ファイルなど） |
| `ORPHAN` / `INCOMPLETE` / `DUPLICATE_EVENT` / `EVENT_WITHOUT_ARTIFACT` | Artifact と Event の対応の崩れ |
| `BAD_EVENT_FILE` / `SEQUENCE_MISMATCH` / `SEQUENCE_GAP` | Event ファイルの破損・番号の食い違い・欠番 |

## 7. Event Log（最小の接続）

- 1 Event = 1ファイル。ファイル名が sequence そのもの（`0000000001.json`）なので、同じ番号の2件目は排他的作成の段階で `EventSequenceCollision` になり、既存のファイルは変わりません。
- Artifact の作成は `ARTIFACT_RECEIVED` として記録します（`ARTIFACT_CREATED` は使いません）。取り込みも同じ種別で、`payload.via = "IMPORT"` です。
- Event Log の全面的な作り直しや、完全な Event Sourcing はしていません。

## 8. 予約済みのシステム記録者 `NODE-studio`

```
NODE-studio
= E001 system actor
= human/AI provider ではない
= Artifact/Event persistence operation を記録するための system identity
```

`ArtifactStore` / `EventLog` 自身が書く Event の `actorNodeId` に使う、**E001 の予約済み ID** です。「Node がまだないので仮に入れた値」ではなく、E001-C の時点ですでに存在する特別な役割です。Schema 上は `node` レコード（kind `SYSTEM`）として登録されている必要があり、bundle の検証はそれがないと参照エラーにします（テスト済み）。E001-D で Node モデルを実装するときに、この予約 ID との整合を確認します。

## 9. 書き出し・取り込み

- **書き出し：** 検証済み・Event 付きの Artifact だけを、`payload.bin` と `artifact.json` のバイト列そのままで新しいフォルダに写し、`export.json`（各 Artifact の contentHash・metadataHash・byteSize）を添えます。空でないフォルダには書きません。
- **取り込み：** すべてのファイルを、何かを書く前に照合します。1件でも問題があれば全体を拒否し、何も書きません。
  - 既存の ID は、`artifactId`・`contentHash`・`metadataHash` の3つがすべて一致するときだけ省略します。
  - 1つでも違えば `COLLISION` として拒否します。**マージも上書きもしません。**
  - 取り込んだ `artifact.json` は、パッケージのバイト列をそのまま書きます（再シリアライズしない）。元の ID と出所（provenance）は変わりません。
- 書き出しパッケージ全体（Case・Run など他のレコードを含むもの）は E001-I で扱います。

## 10. 保証しないこと

- **OS・ファイルシステム水準の改ざん防止。** 管理者や他のプログラムは、読み取り専用属性を外してファイルを書き換えられます。E001-C が保証するのは、そうした変更を**読み出し時のハッシュ照合で検出できる**ことです（Event 自体の改ざんは§3の範囲外）。
- 確定と Event 記録の原子性（§4）。
- 複数のプロセスが同時に書き込む場合の調停。E001 はローカルの単一利用者を前提にしています。sequence と ID の重複は、排他的作成によって拒否されます。
- UTF-8 以外のバイト列の保存（E001-C の実装上の制約。§11 I-3）。
- 参照先（Case・Run・Transfer・Node）が実在するかの、作成時点での確認（bundle の検証で行う。I-7）。

## 11. E001-C の Human Gate で確定した判断

| ID | 判断 |
|---|---|
| I-1 | Artifact に trialId を持たせない。run → trial で辿る |
| I-2 | Artifact に providerId を持たせない。`receivedVia` と transfer / manifest から辿る |
| I-3 | UTF-8 以外の payload は E001-C では拒否する。**Schema 上できないのではなく、E001-C の実装上の制約** |
| I-4 | contentType は E001 では見送る |
| I-5 | Event 種別は `ARTIFACT_RECEIVED` を使い、`ARTIFACT_CREATED` は追加しない |
| I-6 | Event の payload に `metadataHash` を記録する。Event 自体の完全な改ざん耐性は意味しない（§3） |
| I-7 | E001-C の作成時は構造の検証まで。参照先の実在は bundle の検証で確認する |
| I-8 | `NODE-studio` を E001 の予約済みシステム記録者として扱う（§8） |

Schema（`domain/schema/`）は変更していません。

## 12. テストの空振り確認（mutation check）の結果

E001-C の実装時（2026-10-08）に、各保証の検査をメモリ上で1種類ずつ無効にし（ファイルは変更しない）、`tests/persistence/` を実行しました。使ったスクリプトは一時的な検証用のもので、通常のテストとは性質が違うため、リポジトリには入れていません（E001-C Human Gate の判断）。

| 無効にした保証 | 失敗したテスト数 |
|---|---|
| ID の重複拒否 | 4 |
| 上書きの拒否 | 2 |
| payload の破損検出 | 3 |
| metadata の破損検出 | 3 |
| ORPHAN の検出 | 3 |
| INCOMPLETE の検出 | 1 |
| Event の sequence 衝突 | 1 |
| 取り込み時の衝突（COLLISION） | 2 |
| 取り込み時のハッシュ照合 | 1 |
| contentLocation の検査 | 5 |

- 10種類すべてで、少なくとも1件のテストが失敗しました（10/10 で検出）。
- 初回の実行では、payload と metadata の2種類で失敗が0件でした。原因はテストではなく検証用スクリプト側の誤りで、同じモジュールへの2つ目の変更が1つ目を元に戻していました（累積して適用していなかった）。修正して再実行した結果が上の表です。

## 13. 守るべき境界（E001-I に向けて）

```
RAW BYTES
   ↓
Artifact
   ├── contentHash
   ├── metadataHash
   └── provenance
        ↓
ARTIFACT_RECEIVED Event
        ↓
Observation（後続）
```

**Artifact の不変性 ≠ Event Log 全体の不変性。** E001-C が保証するのは前者です。E001-I で Event の鎖を扱うときは、この設計（Artifact の書き込み一回性と、§3 の metadataHash の意味）を変えずに上へ重ねます。
