# ULCS — Universal Language Composition Substrate

ULCS（全語言組合基底）不是把所有程式語言改寫成單一語法，而是讓不同語言保留自身語義，並在同一個可型別化、可驗證、可治理、可重現、可恢復、可由意圖編譯的 Language Operator Graph 中組合。

目前版本：**v0.7.0**

## 核心能力

### 語言與 Runtime

- `ps{}`：PowerShell／可攜式檔案列舉子集
- `regex{}`：正則表達式
- `py{}`：隔離 Python 子程序
- `sql{}`：SQLite
- `bash{}`／`sh{}`：Bash
- `js{}`／`javascript{}`／`node{}`：Node.js
- `jq{}`：jq filter
- `http{}`／`https{}`：HTTP request adapter

### Language Operator Graph

- 單一與多輸入節點
- 前向引用、循環與引用驗證
- 穩定拓樸排序與 `execution_layers`
- 同層有界平行執行
- 多 sink 輸出
- LOG v0.6

### 治理與可觀察性

- audit／enforce 能力政策
- `capability@resource` 資源範圍
- 節點數、工作者與輸出 bytes 配額
- 資料污染來源與 DAG 傳播
- Execution Trace
- Runtime 外掛

### 可重現性與恢復

- Canonical JSON 與 SHA-256 摘要
- `program_digest`、`plan_digest`、`policy_digest`
- 逐節點 fingerprint 與 input／output digest
- 保守內容定址快取
- Execution Manifest 與重放驗證
- Artifact Contract 與受限 schema 驗證
- 內容定址 Artifact Store
- 每層原子 checkpoint
- 部分或完整 resume
- Runtime／cache／resume 來源追蹤

### v0.7 Intent Compiler

- 自然語言 `Intent Request`
- review-first `Intent Plan`
- `ready`／`needs_clarification`／`rejected`
- 生成 `.sos`、Artifact Contract、enforce policy 與 bundle metadata
- 候選產物回送既有 parser、graph、contract、policy validator
- 不自動執行生成產物
- 未辨識或高風險意圖採保守失敗

## 安裝與測試

```bash
python -m pip install -e .
python -m unittest discover -s tests -v
```

```bash
ulcs --list-languages
ulcs --list-capabilities
ulcs-intent --list-profiles
```

舊命令仍可使用：

```bash
sos-mvp examples/error_report.sos --dry-run
```

## `.sos` 語法

```text
<role> <node-id> = <language>{
    <原生語言內容>
} [from <node>[.<field>] [as <name>], ...]
```

角色目前是描述性標籤：`source`、`extract`、`transform`、`store`、`run`。

單一輸入直接傳給 Runtime；多輸入形成具名映射，預設鍵是來源節點名稱，也可以用 `as` 指定。

## v0.7 Intent Compiler 快速範例

Intent Request：

```json
{
  "format": "ULCS-Intent-Request",
  "version": "0.7",
  "intent": "分析以下日誌，找出 ERROR 與 FATAL，統計各類數量。",
  "profile": "log-analysis",
  "bindings": {
    "text": "ERROR one\nINFO two\nFATAL three",
    "terms": ["ERROR", "FATAL"]
  },
  "preferences": {
    "include_matches": true,
    "persist_summary": true
  }
}
```

編譯：

```bash
ulcs-intent examples/intent_request_v0.7.json \
  --output-dir output/v0.7
```

會寫出：

```text
output/v0.7/
  intent-plan.json
  workflow.sos
  artifact-contract.json
  capability-policy.json
  intent-bundle.json
```

Intent Compiler **不直接執行**生成結果。審查後另行執行：

```bash
ulcs output/v0.7/workflow.sos \
  --policy output/v0.7/capability-policy.json \
  --contract output/v0.7/artifact-contract.json \
  --artifact-mode all \
  --artifact-dir output/v0.7/artifacts \
  --yes --json
```

### v0.7 支援的 profiles

#### `log-analysis`

支援內嵌文字或本地檔案、明確 terms、逐行 regex 抽取與分類統計。

典型生成圖：

```text
py or ps source
  → regex matches
  → py summary
```

#### `http-json-fetch`

支援明確 URL 的 GET／HEAD，產生 origin-scoped `network.access` policy。v0.7 不自動生成 POST、PUT、PATCH、DELETE、秘密注入或遠端修改。

完整規格見 [`docs/INTENT_COMPILER_SPEC_v0.7.md`](docs/INTENT_COMPILER_SPEC_v0.7.md)。

## Intent Bundle 狀態

```text
ready
  workflow、contract 與 policy 已通過既有 ULCS validator

needs_clarification
  缺少來源、terms、URL、profile 或安全上必要的明確資訊

rejected
  候選產物未通過 parser、graph、contract 或 policy 驗證
```

`confidence` 只表示 profile 與 bindings 的確定程度，不是正確率或安全證明。

## Artifact Contract

Contract 使用獨立 JSON 文件套用到既有節點，因此 `.sos` 表面語法維持穩定。

每個節點可宣告：

```text
input_schema
output_schema
persist
```

v0.6 支援的 schema 子集：

```text
type
enum
properties
required
additionalProperties
items
minLength / maxLength
minItems / maxItems
minProperties / maxProperties
```

完整規格見 [`docs/ARTIFACT_CONTRACT_v0.6.md`](docs/ARTIFACT_CONTRACT_v0.6.md)。

## Artifact Store

Artifact Reference：

```json
{
  "format": "ULCS-Artifact",
  "version": "0.6",
  "digest": "<content-sha256>",
  "media_type": "application/json",
  "encoding": "utf-8",
  "size": 123,
  "path": "objects/ab/<content-digest>.<schema-digest-or-no-schema>.json",
  "schema_digest": "<sha256-or-null>"
}
```

持久化模式：

- `off`：維持記憶體行為
- `auto`：達大小門檻或 contract 要求時保存
- `all`：保存每個完成節點

輸出必須先通過單節點與累積配額，才會寫入 Artifact Store。

完整規格見 [`docs/ARTIFACT_SPEC_v0.6.md`](docs/ARTIFACT_SPEC_v0.6.md)。

## Checkpoint 與 Resume

Checkpoint 在每個完整拓樸層成功後原子更新，保存 program／plan／policy digest、節點 fingerprint、input／output digest、taints 與 Artifact Reference。

Resume 要求完整計畫、政策、fingerprint、Artifact 與 schema 相容。它不是「看到同名節點就跳過」。

完整規格見 [`docs/CHECKPOINT_RESUME_v0.6.md`](docs/CHECKPOINT_RESUME_v0.6.md)。

## Runtime 外掛

第三方套件可使用 Python entry point：

```toml
[project.entry-points."ulcs.adapters"]
example = "my_ulcs_plugin:ExampleAdapter"
```

或直接載入：

```bash
ulcs workflow.sos --plugin my_ulcs_plugin --dry-run
```

## 版本演化

```text
v0.1  跨語言線性管線
v0.2  Language Operator Graph
v0.3  能力政策與 Runtime 外掛
v0.4  資源範圍、平行 DAG、配額、污染追蹤
v0.5  內容定址快取、Manifest、重放驗證
v0.6  Artifact Contract、schema、checkpoint、resume
v0.7  Intent Request、review-first compiler、生成 bundle
```

## 安全邊界

ULCS v0.7 仍是參考執行層與確定性 Intent Compiler，不是作業系統級沙箱，也不是一般自然語言 AGI 執行器：

- Intent Compiler 不自動執行生成產物。
- `ready` 只表示通過 ULCS validator，不表示業務意圖必然正確。
- 未支援的自然語言不會被猜測成任意程式。
- Python、JavaScript、Bash 與 PowerShell 仍具有啟動程序帳戶的權限。
- 靜態能力與資源推斷無法辨認所有動態行為。
- Artifact Store、cache 與 checkpoint 尚未加密或簽章。
- Checkpoint 不是交易回滾，也不會撤銷已發生的外部副作用。
- Schema 驗證只涵蓋文件列出的受限子集。
- Intent Request 與 Bundle 不應承載秘密。
- 請勿直接執行不可信 `.sos`、contract、policy 或 Runtime 外掛。

正式部署仍應搭配人工或 Agent review gate、容器、低權限帳戶、檔案系統與網路隔離、OS 資源限制、秘密管理、Artifact 加密與生命週期政策。
