# E001 Schema — 検証範囲（Validation Scope）

対象：`domain/schema/studio_schema_e001.json`（schemaVersion `E001/1.0`）と `domain/schema/validator.py`

`validator.py` は **JSON Schema の完全互換実装ではありません**。E001 の保証に必要な範囲だけを、Python 標準ライブラリで検証します。範囲外のキーワードを Schema に書くと、黙って無視せず **Schema の読み込み時にエラー**（`SchemaDefinitionError`）になります。これにより「Schema に書いてあるのに検証されていない規則」が生じません。

## 1. 検証するもの

### 1.1 版

| 項目 | 内容 |
|---|---|
| 対応版 | `E001/1.0` のみ（`SUPPORTED_SCHEMA_VERSIONS`） |
| 不明な版の bundle | `UNSUPPORTED_SCHEMA_VERSION` を1件だけ返し、**それ以上は解釈しない** |
| 不明な版のレコード | bundle 内では `CONST` 違反、単体検証では `UNSUPPORTED_SCHEMA_VERSION` |
| 参考資料の旧 Schema（`E001.0`） | 対応版ではない（受け付けない） |

### 1.2 構造（Schema キーワード）

| キーワード | 扱い |
|---|---|
| `type` | object / array / string / integer / number / boolean / null。配列指定（例 `["string","null"]`）可。**true/false は integer とみなさない** |
| `required` | 必須欄 |
| `properties` | 子の検証 |
| `additionalProperties` | `true` / `false` のみ（Schema 形式は不可）。中心の実体はすべて `false` |
| `enum` / `const` | 値の一致 |
| `pattern` | 文字列のみに適用（`re.search`） |
| `minLength` / `minimum` | 文字列長 / 数値の下限 |
| `items` / `minItems` / `maxItems` | 配列 |
| `format` | `date-time` のみ。RFC 3339 形式かつ**タイムゾーン必須** |
| `$ref` | `#/$defs/<名前>` のローカル参照のみ |
| `$schema` `$id` `title` `description` `$comment` | 注釈（検証に使わない） |
| `$defs` `x-entities` | ルートのみ |
| `x-ref` | E001 拡張：値の ID が、同じ bundle 内の指定実体に存在すること |
| `x-ref-versioned` | E001 拡張：`{id, version}` の組が、同じ bundle 内に存在すること |

### 1.3 参照整合性・重複

- `x-ref` / `x-ref-versioned` の付いた欄は、bundle 内の実在レコードを指す必要があります（`REF` / `VERSIONED_REF`）。null 可の欄は、値があるときだけ検査します。
- ID の重複は拒否します（SR-01）。版のある実体（pattern / route / protocol）は `(id, version)` の組で一意です。同じ ID で版が違うものは可です。

### 1.4 意味規則（Semantic Rules）

JSON Schema では表せないレコード間の規則です。すべて `tests/unit/test_schema.py` に対応するテストがあります。

| ID | 規則 | E002 |
|---|---|---|
| SR-01 | ID 重複の拒否（版のある実体は id+version） | §11, §22 |
| SR-02 | 手動 Provider・手動 Transfer は `ENFORCED` を名乗れない | §16 |
| SR-03 | Source の verificationStatus とハッシュの整合（未検証を検証済みにしない） | §10 |
| SR-04 | Artifact は content / contentLocation のどちらか一方。内蔵 content は UTF-8 バイト列の SHA-256 と byteSize が一致（正規化なし） | §11 |
| SR-05 | Transfer の packet は PACKET 種別で、packetHash がその Artifact のハッシュと一致 | §13, §15 |
| SR-06 | Observation は RAW_RESPONSE を指し、Transfer と同じ Run・宛先 Node | §17 |
| SR-07 | Synthesis の各項目は自分の inputObservationIds だけを出典にし、入力は同じ Run | §18 |
| SR-08 | Event の sequence は一意で、記録順に厳密に増加 | §12 |
| SR-09 | Gate の決定者は HUMAN の Node | §20 |
| SR-10 | Run と Run Manifest が互いを指し、同じ Trial を指す | §8, §9 |
| SR-11 | Route の slotBindings が Pattern の slot をちょうど1回ずつ覆う。edge の両端は束縛済み Node か SYSTEM Node。edgeId は Route 内で一意 | §5, §6 |
| SR-12 | Transfer は、その Run の Manifest が固定した Route の edge に従い、両端が一致 | §6, §13 |
| SR-13 | Manifest の roleAssignments が Pattern の役割と Route の束縛に一致。凍結した Node / Provider が実在 | §9 |
| SR-14 | L1 Evaluation の評価者は SYSTEM の Node（AI の自己申告は L1 にならない） | §19 |
| SR-15 | Trial・Route・Manifest の pattern / route / protocol / case の参照が一致 | §7, §9 |
| SR-16 | Event・Gate・Evaluation の target（type と id の組）が実在 | §12, §20 |
| SR-17 | Artifact の provenance（runId・caseId）が、その Transfer の Run、Run の Trial の Case と一致 | §9, §13（E001-D） |
| SR-18 | Transfer の独立性の主張が、宛先に束縛された Provider の上限を超えない | §16（E001-D） |
| SR-19 | 1つの Trial の Run 数が試行予算（budget.maxRuns）以下 | §7（E001-D） |
| SR-20 | 再試行（retryOfRunId）は元の Run と同じ Trial に属する | §8（E001-D） |
| SR-21 | `NODE-studio` は SYSTEM の Node で、役割の枠に束縛されない（Route・Manifest とも） | §4.3（E001-D、D-18） |

### 1.5 暫定の語彙（値の列挙は検証するが、意味は未確定）

| 欄 | 状態 |
|---|---|
| `source.accessClassification`（PUBLIC / RESTRICTED / UNKNOWN） | D-07：E002 が語彙を定義していないため、E001 実装上の暫定解釈。Protocol / Context Assembly の実装時に見直す可能性がある（`TRACEABILITY.md` D-07） |
| `protocol.rules[].action`（RECEIVE / SUBMIT）と `object.producerRelation` | D-05：E001-F で実際に必要な最小語彙を確認してから確定。自由記述の式は今後も評価しない |

## 2. 検証しないもの（E001-B 時点）

| 項目 | 理由・担当フェーズ |
|---|---|
| JSON Schema の他のキーワード（`oneOf` `anyOf` `if` `patternProperties` `uniqueItems` 等） | 使わない。使うと読み込みエラー |
| `contentLocation` の実ファイルのハッシュ | Schema 検証器ではなく保存層（E001-C `ArtifactStore`）が照合する。`docs/STORAGE_E001-C.md` |
| Artifact の書き込み一回性（上書き拒否） | 保存層の性質。E001-C `ArtifactStore` で実装・テスト済み |
| Event の追記のみ性（過去の Event の改変検出） | 保存層。E001-I |
| Protocol ルールの評価そのもの（誰の packet に何が入るか） | E001-F（Context Assembly） |
| Transfer.artifactIds が Protocol に照らして許可されているか | E001-F |
| Manifest 凍結後の不変性 | 保存層。E001-C/D |
| 状態遷移の正しさ（例：COMPLETED の Run が再開されない） | E001-D 以降 |
| Gate の承認が別の対象に流用されないこと（実行時） | E001-H（Schema では target の実在のみ） |
| 書き出しに秘密情報が含まれないこと | E001-I |
| 単体レコード検証（`validate_record`）での参照整合性 | 文脈がないため構造のみ。参照は `validate_bundle` で検査 |
| OS / ファイルシステム水準の改ざん耐性 | E001 では主張しない |

## 3. 実行方法

```
cd evolution-e001
python -m unittest discover -s tests -t .
python -m domain.schema.validator tests/fixtures/p001_bundle_valid.json
```
