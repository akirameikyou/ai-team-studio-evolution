# TRACEABILITY — E002 → E001 Schema → Implementation → Tests

追跡の連鎖：

```
Accepted Specification E002（docs/、無改変、SHA-256 は SHA256SUMS.txt）
  ↓
E001 Schema（domain/schema/studio_schema_e001.json、schemaVersion E001/1.0）
  ↓
Implementation（E001-B 時点では validator.py のみ。他は各フェーズで追記）
  ↓
Tests（tests/unit/test_schema.py ほか）
```

- 旧 Schema `docs/reference/AI_Team_Studio_STUDIO_SCHEMA_E001.json`（schemaVersion `E001.0`）は **参考資料として無改変で保存** したものです。正式な E001 Schema ではありません。以前のレビューで読んだものと同一かどうかは確定していません（当時のハッシュの記録がないため）。
- 「実装」欄の「—」は、まだ実装していないことを示します。後のフェーズで追記します。

## 1. 実体・概念の対応

| E002 | Schema（`$defs`） | 意味規則 | 実装 | テスト（`test_schema.py`） |
|---|---|---|---|---|
| §4.1 Case | `case` | — | E001-D `application/execution_context.py`（create_case、作成時 Event に recordHash） | `test_every_required_field_is_enforced` ほか、`tests/integration/test_case_trial_run.py` |
| §4.2 Design Space | `designSpace` | — | E001-D `application/execution_context.py` | required、`tests/integration/test_case_trial_run.py` |
| §4.3 Node | `node`（kind: HUMAN/MODEL/TOOL/RESOURCE/SYSTEM） | — | E001-D `application/execution_context.py`（register_node、`NODE-studio` の予約） | `test_role_cannot_be_used_as_node_kind`、`test_case_trial_run.py` |
| §4.4 Role | `role`（enum）、`pattern.slots[].role`、`runManifest.roleAssignments` | SR-13 | — | `test_sr13_*` |
| §4.5 Provider | `provider`（kind: MANUAL_CLIPBOARD/MANUAL_FILE） | SR-02 | E001-D `application/execution_context.py`（register_provider） | `test_future_provider_kind_is_rejected`、`test_sr02_*`、`test_case_trial_run.py` |
| §5 Pattern | `pattern`（topology PARALLEL/SEQUENTIAL、slots） | SR-11 | E001-D `application/execution_context.py`（記録の保存のみ。実行は E001-E） | `test_same_core_holds_p001_and_p002`、`tests/integration/test_p001_p002_records.py` |
| §6 Route | `route`（slotBindings、edges、patternRef、protocolRef） | SR-11, SR-12 | E001-D `application/execution_context.py`（register_route：枠の網羅・edge の両端） | `test_sr11_*`、`test_sr12_*`、`test_p001_p002_records.py` |
| §7 Trial | `trial` | SR-15 | E001-D `application/execution_context.py`（create_trial、予算） | `test_applied_experience_must_be_empty_in_e001`、`test_sr15_*`、`test_case_trial_run.py`、`test_sr19_*` |
| §8 Run | `run`（retryOfRunId） | SR-10 | E001-D `application/execution_context.py`（start_run、状態は Event から導出） | `test_sr10_*`、`test_case_trial_run.py`、`test_sr20_*` |
| §9 Run Manifest | `runManifest` | SR-10, SR-13, SR-15 | E001-D `application/execution_context.py`（Run 開始時に凍結、RUN_STARTED に manifestHash / recordHashes）、`application/provenance.py` reconstruct_run | `test_sr13_*`、`test_sr15_*`、`tests/integration/test_manifest_freeze.py` |
| §10 Source | `source` | SR-03 | E001-D `application/execution_context.py`（register_source：状態は証拠から導出） | `test_sr03_*`、`test_case_trial_run.py` |
| §11 Artifact / RAW | `artifact` | SR-01, SR-04 | E001-C `infrastructure/storage/artifact_store.py`（書き込み一回性・正確なバイト列の SHA-256・読み出し時の照合）、`integrity.py` | `test_sr04_*`、`test_duplicate_artifact_id`、`tests/persistence/test_artifact_store.py` |
| §12 Event | `event` | SR-08, SR-16 | E001-C `infrastructure/storage/event_log.py`（1 Event = 1 ファイル、追記のみ、ARTIFACT_RECEIVED との接続） | `test_sr08_*`、`tests/persistence/test_artifact_event_link.py` |
| §13 Transfer | `transfer`（edgeId、packetHash、externalDelivery、isolationAssurance） | SR-02, SR-05, SR-12 | E001-D `application/execution_context.py`（record_transfer：記録のみ。パケットの組み立ては E001-F、Provider の流れは E001-G） | `test_sr05_*`、`test_sr12_*`、`tests/integration/test_provenance_chain.py`、`test_sr17_*`、`test_sr18_*` |
| §14 Protocol | `protocol`（rules: subject/action/object/effect/condition、DENY 既定、DENY_OVERRIDES） | — | — | `test_protocol_default_must_be_deny`、`test_protocol_condition_is_a_closed_list` |
| §15 Context Assembly | （Schema 上は transfer.artifactIds と packetHash） | SR-05 | E001-F | `test_isolation_secrets_appear_only_in_own_raw_response`（fixture の健全性のみ） |
| §16 Independence Assurance | `isolationAssurance`（4段階） | SR-02 | — | `test_sr02_*` |
| §17 Observation | `observation` | SR-06 | — | `test_sr06_*` |
| §18 Synthesis | `synthesis`（findingItem.observationIds） | SR-07 | — | `test_sr07_*`、`test_synthesis_has_no_majority_field` |
| §19 Evaluation | `evaluation`（L1 のみ） | SR-14 | — | `test_sr14_*` |
| §20 Human Gate | `gate`（targetType/targetId/targetContentHash） | SR-09, SR-16 | — | `test_sr09_*`、`test_sr16_*` |
| §21 Reflection / Experience | 定義しない（E001 の対象外）。`trial.appliedExperienceIds` は空のみ | — | — | `test_unknown_record_set_is_rejected`、`test_applied_experience_must_be_empty_in_e001` |
| §22 Persistence | bundle は検証・書き出し用のまとまりであり、保存の正本ではない | — | E001-C：Artifact / Event のファイル保存と Artifact の書き出し・取り込み（`docs/STORAGE_E001-C.md`）。全体の書き出しは E001-I | `tests/persistence/`（3ファイル） |
| §25 Testing（Persistence: unknown version） | `schemaVersion` const | — | validator | `VersionTests` |
| §26 Migration | 定義しない（E001-B では移行しない） | — | — | — |

## 2. E002 に明記がなく、E001-B で決めた解釈（設計判断）

人間の確認が必要なものには【要確認】を付けています。

| ID | 判断 | 根拠・理由 |
|---|---|---|
| D-01 | schemaVersion を `E001/1.0` とし、`$id` も旧 Schema と変える | 旧 Schema（`E001.0`）と混同しないため |
| D-02 | Case.status は `OPEN / PAUSED / CLOSED` | E002 §4.1「粗く保つ」。値の指定はない |
| D-03 | Role は enum とし、Pattern の slot に付ける。Node.kind に役割を入れない | E002 §4.3–4.4 |
| D-04 | Route は slot を Node と Provider に束縛し、edge は具体的な nodeId を使う。Case の起点は SYSTEM Node（Studio） | E002 §6「concrete graph」。Case 自体は Node ではないため |
| D-05 | **暫定（E001-F で最小語彙を確認してから確定）。** Protocol の action は `RECEIVE / SUBMIT` のみ。object は artifactKinds と producerRelation（ANY/NONE/SELF/ROUTE_PREDECESSOR/PEER）。condition は閉じた列挙（ALWAYS / ROUTE_EDGE_EXISTS）。既定は DENY、競合は DENY_OVERRIDES に固定 | E002 §14「決定的な解決」。実行・委任は E001 の対象外（§3） |
| D-06 | `trial.appliedExperienceIds` は空配列のみ | E001 に Experience 実体がないため（§21） |
| D-07 | **E001 実装上の暫定解釈（E002 の正式定義ではない）。** Source.accessClassification semantics are an E001 implementation interpretation because E002 does not fully define the vocabulary. Current E001 interpretation: PUBLIC / RESTRICTED / UNKNOWN（その資料を誰が見てよいか）. This interpretation may be refined during Protocol / Context Assembly implementation. | E002 §10 は語だけで、語彙と意味の定義がない。既存 Studio の ACCESS（AI が読めたか：DIRECT/READABLE/INACCESSIBLE/UNKNOWN）は観測者ごとの情報なので、Source ではなく Observation 側（`fields`）に属すると判断。E001-B Human Gate（2026-10-08）で暫定解釈として扱うことを確認 |
| D-08 | Artifact の種類は CASE_INPUT / SOURCE_CONTENT / PACKET / RAW_RESPONSE / HUMAN_NOTE。Observation・Synthesis・Evaluation は Artifact ではなく派生レコード | E002 §11「derived artifacts/records」 |
| D-09 | Gate.actionClass に `DECISION`（判断の Gate）を追加。status は E002 の4値のみ（PENDING なし＝未決定の Gate はまだ記録しない）。`targetContentHash` で決定時の内容に結び付けられる | E002 §20。統合の採否のような判断を表すため |
| D-10 | Evaluation は L1 のみ・評価者は SYSTEM | E002 §19 |
| D-11 | Run.status に PAUSED、Transfer.status に TIMED_OUT / UNKNOWN、Transfer に `externalDelivery`（UNKNOWN / HUMAN_REPORTED）を設けた | E002 §13（外部の行為が確認できなければ UNKNOWN）、SHOULD 7 |
| D-12 | Run Manifest は版のある実体を (id, version) で参照し、版のない Node / Provider は凍結した写しを埋め込む | E002 §9「Manifest から再構成できる」 |
| D-13 | bundle（`E001_STORE_BUNDLE` / `E001_EXPORT`）は検証と書き出しのまとまり。正本の保存配置は E001-C / I で決める | E002 §22「巨大な可変 JSON を正本にしない」 |
| D-14 | ID は種類ごとの接頭辞＋パターン。採番方式は E001-C / D で決める | 重複拒否と種類の取り違え防止 |
| D-15 | Event 種別は E002 の12種に `PROTOCOL_VIOLATION` と `SOURCE_REGISTERED` を追加 | AT-009（違反の記録）と §10 |
| D-16 | Provider の将来種別（API / PROXY / LOCAL_LLM / CODE_EXECUTOR）は列挙に入れない。追加は Schema の版を上げて行う | E002 §4.5。未実装のものを受け付けないため |
| D-17 | 版の方針：実データの正本運用が始まる前（E001-C より前）は、意味を明確にするための修正を `E001/1.0` のまま行う。既存データの解釈の変更、必須欄の変更、既存レコードとの非互換、Artifact / Source / Protocol の構造そのものの変更が必要になったときは、版の変更を Human Gate に戻す | E001-B Human Gate（2026-10-08） |
| D-18 | `NODE-studio` は E001 の予約済みシステム Node（kind SYSTEM）。**E001-D で定義を拡張：** Studio 自身が行う system-level operation（Artifact の保存、Event の記録、Context / パケットの送出）の主体。人間でも AI モデルでも Provider でも Role でもなく、観測者にはならない。Route の起点であることは「Studio が入力パケットを渡した」という運搬・システム操作の主体を意味し、「Studio が回答を生成した」ことは意味しない。パケットの作者は常に `NODE-studio` | E001-C Human Gate（I-8）、E001-D Human Gate（K-3, 2026-10-08）。`docs/EXECUTION_CONTEXT_E001-D.md` §7 |
| D-19 | E001-C の判断 I-1〜I-7（trialId・providerId を Artifact に持たせない、UTF-8 以外は実装上の制約として拒否、contentType 見送り、`ARTIFACT_RECEIVED` を使用、`metadataHash` は Event Log の改ざん検出の鎖ではない、参照先の実在は bundle の検証で確認）。Schema は変更しない | E001-C Human Gate（2026-10-08）。`docs/STORAGE_E001-C.md` §11 |
| D-20 | レコードは作成時の内容のまま書き換えない。現在の状態（Run の RUNNING / COMPLETED / FAILED）は Event から導く。snapshot は E001-D では実装しない | E001-D Human Gate K-2。E002 §22 |
| D-21 | Manifest の凍結の時点は Run の開始時。Schema に開始前の Run の状態がないため「作成≈開始」とする **E001-D の制約**。PLANNED / READY 等が必要になれば Schema の変更として Human Gate へ | E001-D Human Gate K-4 |
| D-22 | E001-D の範囲：Manifest の凍結に必要な Node・Provider・Pattern・Route・Protocol・DesignSpace・Source を「記録として保存する」まで含める。Transfer は記録の保存のみ。Pattern の実行（E）、Protocol の評価とパケットの組み立て（F）、Provider の流れ（G）は含めない | E001-D Human Gate K-1 |
| D-23 | 既知の制約：作成時の Event がない記録（node・provider・pattern・route・protocol・designSpace）は、登録から Manifest の凍結（RUN_STARTED）までの間、Event による改ざんの証拠がない。E001-I の Event の鎖で強化する | E001-D Human Gate K-6 |
| D-24 | 既知の制約：応答の Artifact の `provenance.transferId` は1つの Transfer しか指せない。複数の edge から入力を受ける Node（P002 の B など）のパケットの表し方は E001-F / G で決める | E001-D 実装時に判明 |
