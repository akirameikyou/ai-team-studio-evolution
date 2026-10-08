# EXECUTION CONTEXT — E001-D

対象：`application/execution_context.py`、`application/provenance.py`、`infrastructure/storage/record_store.py`、検証器の SR-17〜21

E001-C は「Studio が何を受け取ったか」を証拠として保存しました。E001-D は、その証拠を「誰が・どの役割で・どの Provider を通じ・どの Trial / Run で・どの Pattern / Route / Transfer を経て」受け取ったのか、という実行の文脈の中に位置づけます。

```
Artifact  = 証拠（E001-C）
Run       = 実行の文脈
Manifest  = その実行の、凍結された記述
Transfer  = 実際に起きた移動
Route     = 設計上の経路
Pattern   = 配置
```

**この層は記録するだけです。** 外部への行為（シェル、Git、公開、API 呼び出し）は行いません。パケットの組み立て（E001-F）と Provider の流れ（E001-G）もまだ含みません。

## 1. 層の分け方

| 層 | 役割 | 中身 |
|---|---|---|
| `infrastructure/storage/record_store.py` | **保存の仕組みだけ** | Schema のレコードを1回だけ書き、読み戻して照合する。Case や Run が何を意味するかは知らない |
| `application/execution_context.py` | **意味** | Case・Trial・Run の作成、Manifest の凍結、Transfer の記録、状態の導出 |
| `application/provenance.py` | **読み取りだけ** | 上下方向の追跡、Run の再構成の可否、P001 の独立性の報告、ストア全体の検証 |

RecordStore は「何でも入れる汎用モデル」ではありません。各レコードの意味は application 層にあります。Artifact と Event は RecordStore に入れません（E001-C の ArtifactStore と EventLog が持ちます）。

## 2. 保存の構成（E001-C に追加）

```
<store>/records/<entity>/<id>.json                case, designSpace, node, provider, source, trial, run, runManifest, transfer
<store>/records/<entity>/<id>/<version>.json      pattern, route, protocol
```

- どのレコードも1回だけ書き込み、Schema で検証してから書き、ID の重複は拒否します。
- 版のある実体は `(id, version)` の組で一意です。
- ID と版は、パスに使う前に形式を確かめます。そのため `records/` の外は読めません（テストで、実在するファイルへの迂回で確認）。

## 3. 状態は Event から導く（K-2 / D-20）

レコードは作成時の内容のまま、書き換えません。現在の状態は Event から導きます。

| レコード | ファイル上の status | 現在の状態の出どころ |
|---|---|---|
| Run | 作成時の `RUNNING` のまま | `RUN_STARTED` / `RUN_COMPLETED` / `RUN_FAILED` → RUNNING / COMPLETED / FAILED。`RUN_STARTED` がなければ `UNANCHORED` |
| Trial | 作成時の `ACTIVE` のまま | E001-D では状態変化を記録しない（対応する Event 種別がない） |
| Case | 作成時の `OPEN` のまま | 同上 |
| Transfer | 作成時の値（既定 `HANDED_TO_HUMAN`）のまま | 応答の受け取りは、`provenance.transferId` を持つ Artifact の `ARTIFACT_RECEIVED` で分かる |

`run.json` を `COMPLETED` に書き換えることはしません（テスト済み）。snapshot（作り直せる要約）は E001-D では実装していません。

## 4. Manifest の凍結（K-4 / D-21）

**凍結の時点は Run の開始時です。** 現在の Schema には開始前の Run の状態がないため、「Run の作成」と「Run の開始」は同じ時点になります。これは理想の最終形ではなく、**E001-D の制約**として採用しています。PLANNED や READY のような状態が必要になったら、Schema の変更を含めて Human Gate に戻します。

`start_run` の手順：

1. 試行予算（`maxRuns`）と、再試行が同じ Trial に属することを確認する。
2. Manifest を書く：case・trial・pattern / route / protocol の (id, version)、Node と Provider の凍結した写し、役割の割り当て、Source の状態、最初に許可する Artifact、`configuration`。
3. Run のレコードを書く。
4. `RUN_STARTED` Event を記録する。payload は次のとおり。
   - `manifestHash`
   - `recordHashes`：凍結した case・trial・designSpace・pattern・route・protocol・各 node・各 provider・各 source のレコードのハッシュ
   - `initialArtifactHashes`

途中で止まった場合は、隠さずに検出できる状態として残ります。

- 2 と 3 の間 → `ORPHAN_MANIFEST`（どの Run も指していない Manifest）
- 3 と 4 の間 → Run の状態が `UNANCHORED`、ストアの検証では `UNANCHORED_RUN`

## 5. 出所の追跡（provenance）

```
上へ： Artifact.provenance.transferId → Transfer.runId → Run.trialId → Trial.caseId → Case
下へ： Case ← trial.caseId ← run.trialId ← transfer.runId ← artifact.provenance.transferId（逆引き）
```

- つながりは ID の参照だけで作り、一覧の複製はしません。
- `trace_artifact` は各段のつながりを照合します。次の場合は `PROVENANCE_MISMATCH` を返します。
  - Artifact が名乗る Run と、その Transfer の Run が違う
  - Case が Trial と違う
  - 応答の作者が、パケットの宛先でない
  - パケットの作者が `NODE-studio` でない
- `trace_case` は、Case → Trial → Run（現在の状態つき）→ Transfer → 応答の Artifact を返します。
- `reconstruct_run` は、Manifest と凍結した各レコードが `RUN_STARTED` のハッシュと一致し、その Run の Transfer と Artifact がすべて照合を通るかを確かめます。**再実行の仕組みではありません。** 再構成に必要な出所の情報が壊れていないことを確かめるものです。

## 6. Node・Role・Provider

- **Node**：誰が。役割も Provider も持たない（テストで、Node のレコードの欄を確認）。
- **Role**：何をするか。Pattern の枠（slot）に付く。
- **Provider**：どの経路で動かすか。Route の slotBindings で、枠を Node と Provider に束縛する。
- 手動の Provider は Human の Node ではありません。人間は Node（HUMAN）として、統合役や Gate などの役割を担います。

## 7. `NODE-studio`：Studio 自身を表すシステム Node（K-3 / D-18 を拡張）

E001-C での定義「保存操作を記録するための ID」を、E001-D で次のように**正式に広げました**。

```
NODE-studio
TYPE = SYSTEM
= Studio 自身が行う system-level operation の主体
    - Artifact の保存
    - Event の記録
    - Context / パケットの送出（Route の起点、パケットの作者）
≠ Human
≠ AI Model
≠ Provider
≠ Role
```

- **NODE-studio は観測者ではありません。** P001 の観測者 A/B/C と同列の Observer ではなく、役割の枠に束縛することはできません（アプリが拒否し、SR-21 でも検出）。
- Route の起点が `NODE-studio` であることは、「Studio が AI の回答を生成した」という意味ではありません。**「Studio が入力パケットを観測者へ渡した」という、運搬・システム操作の主体**であることを意味します。
- P002 で edge が A→B の場合も、パケットを組み立てて渡すのは Studio です。そのため、パケットの作者は常に `NODE-studio` です（edge の起点 A は「情報の出どころ」を表す）。
- ストアを開くたびに、予約済みのレコード（kind SYSTEM）が存在し、内容が一致することを確かめます。異なれば開きません（`SystemActorConflict`）。

## 8. Pattern・Route・Transfer

- Pattern と Route は (id, version) ごとに変わりません。Route を登録するときに、Pattern の枠をちょうど1回ずつ覆うこと、edge の両端が束縛された Node か SYSTEM の Node であることを確かめます。
- Transfer は実際の移動の記録です。`record_transfer` は次の場合に拒否します。
  - Run が RUNNING でない
  - edge がその Run の Route にない
  - パケットが PACKET 種別でない、またはパケットの provenance が他の Run や Transfer を指している
  - 運ぶ Artifact の種類が edge に許可されていない
  - 運ぶ Artifact がまだ存在しない（**そのため、Transfer は自分が運ぶものより先には記録できない**。P002 の順序はここから生じる）
  - 独立性の主張が Provider の上限を超えている
- `method` は宛先に束縛された Provider の種類から取り、`externalDelivery` は `UNKNOWN` で記録します（外部で何が起きたかは確認できないため）。

## 9. P001 の独立性（記録から確かめる）

`independence_report(run)` は、観測者宛ての各 Transfer について、運んだ Artifact と、そのうち「同じ Run の他の観測者の回答」を一覧にします。

- 通常の P001 の Route では、観測者への edge が CASE_INPUT と SOURCE_CONTENT しか許可しないため、他の観測者の回答はそもそも記録できません（テスト済み）。
- Route が通してしまう場合は、報告が `PEER_OBSERVATION_IN_PACKET` として検出します（テスト済み）。
- **これは検出であり、強制ではありません。** Protocol による強制は E001-F で行います。
- 手動経路の保証レベルは `PACKET_CONTROLLED` で、`ENFORCED` にはなりません。

## 10. 既知の制約

- **登録から凍結までの改ざん（K-6 / D-23）：** node・provider・pattern・route・protocol・designSpace には作成時の Event がありません。そのため、Run の Manifest に凍結されて `RUN_STARTED` にハッシュが記録されるまでの間は、**Event による改ざんの証拠がまだ存在しません**。登録直後から凍結までの期間は、これが既知の制約です。E001-I の Event の鎖で、より強い証拠構造へ進めます。（case・trial・source・transfer は作成時の Event に `recordHash` があり、`verify_store` が照合します。）
- **複数の入力を受ける Node（D-24）：** P002 の B は `case-b` と `a-to-b` の2つの Transfer を受け取りますが、応答の Artifact の `provenance.transferId` は1つしか指せません。E001-D のテストでは、最後の Transfer（`a-to-b`）を指しています。複数の edge をまとめた1つのパケットをどう表すかは、E001-F / G で決める必要があります。
- Trial と Case の状態変化、Run の PAUSED / CANCELLED、Transfer の FAILED / TIMED_OUT / CANCELLED は、対応する Event 種別がないため記録できません（必要になった時点で、Schema の変更として Human Gate へ）。
- 手動で中身を運んだ人間は記録しません（UNKNOWN）。
- 複数のプロセスによる同時書き込みの調停はしません。ID と sequence の重複は、排他的な作成によって拒否されます。

## 11. テストの空振り確認（mutation check）

E001-D の実装時（2026-10-08）に、19種類の保証を1つずつメモリ上で無効にし、テスト全体（172件）を実行しました。スクリプトは一時的な検証用のもので、リポジトリには入れていません。

| 無効にした保証 | 失敗したテスト数 |
|---|---|
| レコードの書き込み一回性 | 4 |
| レコードの ID 不一致の検出 | 1 |
| レコード ID によるパス迂回の防止 | 1（※） |
| Manifest の凍結照合 | 1 |
| 凍結したレコードの照合 | 2 |
| 作成時 Event による照合（recordHash） | 1 |
| SR-17 / SR-18 / SR-19 / SR-20 / SR-21 | 3 / 1 / 1 / 1 / 2 |
| 試行予算 | 1 |
| 再試行は同じ Trial | 1 |
| 状態を Event から導くこと | 3 |
| `NODE-studio` の予約 | 3 |
| edge が許可する Artifact の種類 | 3 |
| パケットと Transfer の結び付け | 2 |
| 追跡時のつながりの照合 | 2 |
| 独立性の報告 | 1 |

19/19 で検出しました。

※ 初回はパス迂回の防止を無効にしても失敗が0件でした。テストが試していたパスが実在しなかったため、ID の検査がなくても「見つからない」で止まっていたからです。迂回先が**実在する**ケースをテストに加え、再実行で検出できることを確認しました。
