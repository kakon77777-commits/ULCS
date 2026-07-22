# ULCS — Universal Language Composition Substrate

ULCS（全語言組合基底）不是把所有程式語言改寫成單一語法，而是讓不同語言保留自身語義，並在同一個可型別化、可驗證、可治理、可重現的 Language Operator Graph 中組合。

目前版本：**v0.5.0**

## v0.5 核心能力

### 語言與 Runtime

- `ps{}`：PowerShell／可攜式檔案列舉子集
- `regex{}`：正則表達式
- `py{}`：隔離 Python 子程序
- `sql{}`：SQLite
- `bash{}`／`sh{}`：Bash
- `js{}`／`javascript{}`／`node{}`：Node.js
- `jq{}`：jq filter
- `http{}`／`https{}`：HTTP request adapter

### 計算圖

- 單一與多輸入節點
- 前向引用
- DAG 循環與引用驗證
- 穩定拓樸排序與 `execution_layers`
- 同層有界平行執行
- 多 sink 輸出
- LOG v0.5

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
- 逐節點 input／output digest
- 內容定址快取
- deterministic／cacheable 驗證標記
- Execution Manifest
- 重放驗證

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

## v0.5 快取與重放範例

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

第一次執行並建立基準：

```bash
ulcs examples/cache_replay_v0.5.sos \
  --policy examples/cache_policy_v0.5.json \
  --cache-mode read-write \
  --cache-dir output/cache-v0.5 \
  --yes --json \
  --emit-ir output/cache-v0.5.json \
  --emit-trace output/cache-v0.5-first.json \
  --emit-manifest output/cache-v0.5-manifest.json
```

第二次執行並驗證：

```bash
ulcs examples/cache_replay_v0.5.sos \
  --policy examples/cache_policy_v0.5.json \
  --cache-mode read-write \
  --cache-dir output/cache-v0.5 \
  --yes --json \
  --verify-manifest output/cache-v0.5-manifest.json \
  --emit-trace output/cache-v0.5-second.json
```

在此範例中，Python 節點仍由 Runtime 執行；Regex 節點可以命中內容定址快取。重放驗證不比較 `cache_hit`，但會比較輸入、輸出、計畫、政策與污染摘要。

## 三層摘要

### Program digest

固定工作流定義：

```text
node id + role + language + code + input references
```

### Plan digest

固定驗證後計畫：

```text
runtime + types + effects + claims + taint sources
+ deterministic/cacheable + execution layers
```

### Node fingerprint

固定單次節點計算：

```text
validated node definition + runtime + claims + actual input digest
```

因此上游輸出改變時，下游快取鍵也會改變。

## 快取模式

```text
off        不讀、不寫
read       只讀
write      只寫
read-write 先讀，未命中後寫入
```

預設為 `off`，保留 v0.4 執行行為。

```bash
ulcs workflow.sos \
  --cache-mode read-write \
  --cache-dir .ulcs-cache \
  --yes
```

只有 `deterministic=true` 且 `cacheable=true` 的節點能使用快取。v0.5 保守排除 filesystem、network、database 與 process-spawn 效果。

目前內建主要可快取節點為 Regex 與 jq。Python、JavaScript、Bash、PowerShell、HTTP 與 SQLite 預設不快取。

完整規格：[`docs/CACHE_SPEC_v0.5.md`](docs/CACHE_SPEC_v0.5.md)。

## Execution Manifest

```json
{
  "format": "ULCS-Execution-Manifest",
  "version": "0.5",
  "program_digest": "...",
  "plan_digest": "...",
  "policy_digest": "...",
  "execution_layers": [["text"], ["errors"], ["summary"]],
  "nodes": {
    "errors": {
      "fingerprint": "...",
      "input_digest": "...",
      "output_digest": "...",
      "runtime": "python-re",
      "taints": [],
      "deterministic": true,
      "cacheable": true,
      "cache_hit": false
    }
  }
}
```

Manifest 不包含完整 `outputs`。重放驗證比較：

- program／plan／policy digest
- execution layers 與節點順序
- 每節點 fingerprint
- input／output digest
- Runtime identity
- 傳播後 taints

完整規格：[`docs/EXECUTION_MANIFEST_v0.5.md`](docs/EXECUTION_MANIFEST_v0.5.md)。

## LOG v0.5

LOG v0.5 新增：

```text
deterministic
cacheable
```

並保留：

```text
nodes, edges, execution_order, execution_layers, sinks
input_type, output_type, effects, capabilities, runtime
claims, taint_sources
```

```bash
ulcs examples/cache_replay_v0.5.sos \
  --policy examples/cache_policy_v0.5.json \
  --dry-run \
  --emit-ir output/cache-v0.5.json
```

完整規格：[`docs/LOG_SPEC_v0.5.md`](docs/LOG_SPEC_v0.5.md)。

## 平行 DAG

```sos
source left = py{
result = {"value": 20}
}

source right = py{
result = {"value": 22}
}

transform merged = py{
result = {"sum": input["left"]["value"] + input["right"]["value"]}
} from left, right
```

LOG：

```json
{
  "execution_layers": [
    ["left", "right"],
    ["merged"]
  ]
}
```

同一層可以平行；下一層等待前一層全部完成。資料庫寫入與檔案寫入／刪除使用保守序列鎖。

## 能力與資源範圍

```text
network.access@https://api.example.com
filesystem.read@./data/*
python.execute@runtime://python
database.write@sqlite://workflow
```

沒有 `@` 的舊式規則等價於 `capability@*`。無法靜態決定的資源會形成 `@*` claim；enforce 政策必須明確接受它。

完整規格：[`docs/RESOURCE_POLICY_v0.4.md`](docs/RESOURCE_POLICY_v0.4.md)。

## 執行配額

```bash
ulcs workflow.sos \
  --max-nodes 64 \
  --max-workers 4 \
  --max-output-bytes 1048576 \
  --max-total-output-bytes 4194304 \
  --yes
```

預設 `max_workers=1`。節點數在 Runtime 啟動前檢查；輸出大小在值產生後檢查，不構成副作用回滾。

## 資料污染追蹤

LOG 記錄節點可能引入的來源；Execution Trace 與 Manifest 記錄傳播後標籤。

```json
{
  "taints": {
    "remote": ["external.network:https://api.example.com"],
    "derived": ["external.network:https://api.example.com"]
  }
}
```

ULCS 不會因為資料經過清洗、摘要或轉換就自動去污。

## Runtime 外掛

第三方套件可使用 Python entry point：

```toml
[project.entry-points."ulcs.adapters"]
example = "my_ulcs_plugin:ExampleAdapter"
```

外掛可以選擇宣告：

```python
class ExampleAdapter(LanguageAdapter):
    deterministic = True
```

這是受信任契約。錯誤的 deterministic 聲明可能造成不正確快取。即使 deterministic，具有外部資源 effects 的節點仍不會被 v0.5 核心標記為 cacheable。

既有外掛契約：[`docs/RUNTIME_PLUGIN_SPEC_v0.3.md`](docs/RUNTIME_PLUGIN_SPEC_v0.3.md)。

## 安全邊界

ULCS v0.5 是授權、配額、追蹤、快取與重放比較層，不是作業系統級沙箱：

- Python、JavaScript、Bash 與 PowerShell 仍具有啟動程序帳戶的權限。
- 靜態能力與資源推斷無法辨認所有動態行為。
- deterministic 是 Runtime 契約，不是形式證明。
- 快取包含完整節點輸出，可能含秘密或個資；v0.5 不加密、不遮罩、不提供 TTL。
- Manifest 沒有簽章，不能證明主機或文件未被竄改。
- 外部檔案、資料庫與網路狀態尚未自動快照。
- 輸出配額不等於程序記憶體、CPU、檔案大小或網路流量限制。
- 請勿直接執行不可信 `.sos` 或載入不可信外掛。

正式部署仍應搭配容器、低權限帳戶、檔案系統與網路隔離、OS 資源限制、秘密管理、依賴鎖定與受控快取儲存。
