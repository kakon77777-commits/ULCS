# ULCS — Universal Language Composition Substrate

ULCS（全語言組合基底）不是把所有程式語言改寫成單一語法，而是讓不同語言保留自身語義，並在同一個可型別化、可驗證、可治理、可重現、可恢復的 Language Operator Graph 中組合。

目前版本：**v0.6.0**

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
- 前向引用
- DAG 循環與引用驗證
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

### 可重現性

- Canonical JSON 與 SHA-256 摘要
- `program_digest`、`plan_digest`、`policy_digest`
- 逐節點 input／output digest 與 execution fingerprint
- 保守內容定址快取
- Execution Manifest
- 重放驗證

### v0.6 Artifact 與恢復

- Artifact Contract sidecar
- input／output schema 驗證
- 內容定址 Artifact Store
- `off`／`auto`／`all` 持久化模式
- Artifact digest、size、media type、encoding 與 schema digest
- 每層原子 checkpoint
- 部分或完整 resume
- Runtime／cache／resume 三種來源追蹤
- Artifact 路徑逃逸、內容篡改與契約不一致拒絕

## 安裝與測試

```bash
python -m pip install -e .
python -m unittest discover -s tests -v
```

```bash
ulcs --list-languages
ulcs --list-capabilities
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

## v0.6 Artifact 快速範例

工作流：

```sos
source text = py{
result = "ERROR one\nINFO two\nERROR three"
}

extract errors = regex{
ERROR.*
} from text

transform summary = py{
result = {
    "count": len(input),
    "matches": [item["match"] for item in input],
}
} from errors
```

Artifact Contract：

```json
{
  "format": "ULCS-Artifact-Contract",
  "version": "0.6",
  "nodes": {
    "text": {
      "output_schema": {"type": "string"}
    },
    "errors": {
      "input_schema": {"type": "string"},
      "output_schema": {"type": "array"}
    },
    "summary": {
      "input_schema": {"type": "array"},
      "output_schema": {
        "type": "object",
        "required": ["count", "matches"]
      },
      "persist": true
    }
  }
}
```

第一次執行並建立 checkpoint：

```bash
ulcs examples/artifact_resume_v0.6.sos \
  --policy examples/artifact_policy_v0.6.json \
  --contract examples/artifact_contract_v0.6.json \
  --artifact-mode all \
  --artifact-dir output/v0.6/artifacts \
  --checkpoint output/v0.6/checkpoint.json \
  --emit-ir output/v0.6/ir.json \
  --emit-trace output/v0.6/first.json \
  --emit-manifest output/v0.6/manifest.json \
  --yes --json
```

從 checkpoint 恢復：

```bash
ulcs examples/artifact_resume_v0.6.sos \
  --policy examples/artifact_policy_v0.6.json \
  --contract examples/artifact_contract_v0.6.json \
  --artifact-dir output/v0.6/artifacts \
  --resume output/v0.6/checkpoint.json \
  --verify-manifest output/v0.6/manifest.json \
  --emit-trace output/v0.6/resumed.json \
  --yes --json
```

第二次執行若計畫、政策、fingerprint、Artifact 與 schema 全部相符，節點來源會顯示 `RESUME`，而不是再次啟動 Runtime。

## Artifact Contract

Contract 使用獨立 JSON 文件套用到既有節點，因此 `.sos` 表面語法維持穩定。

每個節點可宣告：

```text
input_schema
output_schema
persist
```

v0.6 支援的 schema 子集包括：

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

這不是完整 JSON Schema 實作。完整規格見 [`docs/ARTIFACT_CONTRACT_v0.6.md`](docs/ARTIFACT_CONTRACT_v0.6.md)。

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

- `off`：維持 v0.5 記憶體行為；
- `auto`：達到 `--artifact-threshold-bytes` 或 contract 要求時保存；
- `all`：保存每個完成節點。

相同內容與相同 schema 可共用 Artifact；相同內容若使用不同 schema，內容 digest 相同，但物件路徑不同。輸出必須先通過單節點與累積配額，才會寫入 Artifact Store。

Artifact 是可驗證產物，不等同 cache。非 deterministic 節點也能產生 Artifact，但不能因此被一般快取任意跳過。

完整規格見 [`docs/ARTIFACT_SPEC_v0.6.md`](docs/ARTIFACT_SPEC_v0.6.md)。

## Checkpoint 與 Resume

Checkpoint 在每個完整拓樸層成功後原子更新，並保存：

```text
program_digest
plan_digest
policy_digest
node fingerprint
input/output digest
taints
Artifact Reference
```

Resume 要求完整計畫與政策相容，並重新驗證 Artifact 與 schema。它不是「看到同名節點就跳過」。

完整規格見 [`docs/CHECKPOINT_RESUME_v0.6.md`](docs/CHECKPOINT_RESUME_v0.6.md)。

## 快取、Artifact 與 Resume

```text
Cache
  相同 validated node + 相同 input 可否省略重算

Artifact
  已產生值能否持久化、驗證與引用

Resume
  指定 checkpoint 是否能在同一相容計畫中恢復已完成節點
```

來源追蹤：

```text
RUNTIME
CACHE
RESUME
```

Manifest 重放驗證比較結果與 Artifact digest，但刻意不要求三種來源相同。

## LOG v0.6

新節點欄位：

```text
input_schema
output_schema
persist_output
```

Execution Trace 新增：

```text
resumed
artifacts
checkpoint_path
```

Manifest node record 新增：

```text
resumed
artifact_digest
schema_digest
```

完整規格見 [`docs/LOG_SPEC_v0.6.md`](docs/LOG_SPEC_v0.6.md)。

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

既有 Runtime Adapter 不需要為 v0.6 修改；Artifact Contract 位於執行層外部。

## 版本演化

```text
v0.1  跨語言線性管線
v0.2  Language Operator Graph
v0.3  能力政策與 Runtime 外掛
v0.4  資源範圍、平行 DAG、配額、污染追蹤
v0.5  內容定址快取、Manifest、重放驗證
v0.6  Artifact Contract、schema、checkpoint、resume
```

## 安全邊界

ULCS v0.6 仍是參考執行層，不是作業系統級沙箱：

- Python、JavaScript、Bash 與 PowerShell 仍具有啟動程序帳戶的權限。
- 靜態能力與資源推斷無法辨認所有動態行為。
- Artifact Store、cache 與 checkpoint 尚未加密或簽章。
- Artifact 可能包含完整敏感輸出。
- Checkpoint 不是交易回滾，也不會撤銷已發生的外部副作用。
- Resume 重用非 deterministic 節點的已保存結果，不代表外部世界未改變。
- Schema 驗證只涵蓋文件列出的受限子集。
- 請勿直接執行不可信 `.sos`、contract 或 Runtime 外掛。

正式部署仍應搭配容器、低權限帳戶、檔案系統與網路隔離、OS 資源限制、秘密管理、Artifact 加密與生命週期政策。
