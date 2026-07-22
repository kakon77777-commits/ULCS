# ULCS — Universal Language Composition Substrate

ULCS（全語言組合基底）不是把所有程式語言改寫成單一語法，而是讓不同語言保留自身語義，並在同一個可型別化、可驗證、可治理的 Language Operator Graph 中組合。

目前版本：**v0.4.0**

## v0.4 已完成

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
- 穩定拓樸排序
- 顯式 `execution_layers`
- 同層有界平行執行
- 多 sink 輸出
- LOG v0.4

### 治理與可觀察性

- audit／enforce 能力政策
- `capability@resource` 資源範圍
- 舊式能力 wildcard 相容
- 完整 DAG 執行前授權
- 節點數、工作者與輸出 bytes 配額
- 資料污染來源與 DAG 傳播
- Execution Trace
- Runtime 外掛
- Windows／Ubuntu、Python 3.11–3.13 CI

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

## v0.4 平行 DAG

```sos
source left = py{
result = {"value": 20}
}

source right = py{
result = {"value": 22}
}

transform merged = py{
result = input["left"]["value"] + input["right"]["value"]
} from left, right
```

LOG 將顯示：

```json
{
  "execution_layers": [
    ["left", "right"],
    ["merged"]
  ]
}
```

執行：

```bash
ulcs examples/parallel_v0.4.sos \
  --policy examples/resource_policy_v0.4.json \
  --yes --json \
  --emit-ir output/parallel-v0.4.json \
  --emit-trace output/parallel-v0.4-trace.json
```

同一層可以平行；後一層必須等待前一層全部完成。資料庫寫入與檔案寫入／刪除使用保守序列鎖。

## 能力與資源範圍

v0.3 規則仍有效：

```text
network.access
python.*
```

v0.4 可以限制資源：

```text
network.access@https://api.example.com
filesystem.read@./data/*
python.execute@runtime://python
database.write@sqlite://workflow
```

政策範例：

```json
{
  "mode": "enforce",
  "allow": [
    "network.access@https://api.example.com",
    "python.execute@runtime://python"
  ],
  "deny": [
    "network.access@http://*",
    "filesystem.delete@*"
  ],
  "limits": {
    "max_nodes": 64,
    "max_workers": 4,
    "max_output_bytes": 1048576,
    "max_total_output_bytes": 4194304
  }
}
```

沒有 `@` 的規則等價於 `capability@*`。無法靜態決定的資源會形成 `@*` claim；enforce 政策必須明確接受它。

判定優先序：

1. `deny`
2. `allow`
3. enforce 模式中的未授權 claim
4. audit 模式中的稽核 claim

完整規格見 [`docs/RESOURCE_POLICY_v0.4.md`](docs/RESOURCE_POLICY_v0.4.md)。

## 執行配額

CLI 可覆寫政策：

```bash
ulcs workflow.sos \
  --max-nodes 64 \
  --max-workers 4 \
  --max-output-bytes 1048576 \
  --max-total-output-bytes 4194304 \
  --yes
```

預設 `max_workers=1`，因此既有工作流不會突然改變執行時序。節點數在任何 Runtime 啟動前檢查；輸出大小必須在資料產生後檢查，不構成副作用回滾。

## 資料污染追蹤

LOG v0.4 為節點記錄自身可能引入的來源：

```json
{
  "taint_sources": [
    "external.network:https://api.example.com"
  ]
}
```

Execution Trace 會沿 DAG 傳播標籤：

```json
{
  "taints": {
    "remote": ["external.network:https://api.example.com"],
    "derived": ["external.network:https://api.example.com"]
  }
}
```

v0.4 不會自動去污。驗證、清洗或摘要不會自行消除來源標籤。

完整規格見 [`docs/EXECUTION_TRACE_v0.4.md`](docs/EXECUTION_TRACE_v0.4.md)。

## LOG v0.4

```bash
ulcs examples/parallel_v0.4.sos \
  --policy examples/resource_policy_v0.4.json \
  --dry-run \
  --emit-ir output/parallel-v0.4.json
```

新增欄位：

- `execution_layers`
- `claims`
- `taint_sources`

保留欄位：

- `nodes`
- `edges`
- `execution_order`
- `sinks`
- `input_type`／`output_type`
- `effects`
- `capabilities`
- `runtime`

完整規格見：

- [`docs/LOG_SPEC_v0.4.md`](docs/LOG_SPEC_v0.4.md)
- [`docs/LOG_SPEC_v0.3.md`](docs/LOG_SPEC_v0.3.md)
- [`docs/LOG_SPEC_v0.2.md`](docs/LOG_SPEC_v0.2.md)

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

模組提供：

```python
ULCS_ADAPTERS = [ExampleAdapter()]
```

或：

```python
def register():
    return [ExampleAdapter()]
```

完整契約見 [`docs/RUNTIME_PLUGIN_SPEC_v0.3.md`](docs/RUNTIME_PLUGIN_SPEC_v0.3.md)。v0.4 的資源分析層可為尚未實作 scoped claim API 的既有外掛產生保守 `@*` claim。

## HTTP 適配器

HTTP 適配器：

- 只接受 HTTP／HTTPS
- 拒絕 localhost、`.local` 與解析後的非公網 IP
- 重新驗證 redirect 目標
- 回應限制為 2 MiB
- 可用 `ULCS_HTTP_ALLOW_HOSTS` 設定主機 wildcard allowlist
- 仍需能力政策允許對應 `network.access@<origin>`

## 安全邊界

ULCS v0.4 是授權、配額、追蹤與參考執行層，不是作業系統級沙箱：

- Python、JavaScript、Bash 與 PowerShell 仍具有啟動程序帳戶的權限。
- 靜態能力與資源推斷無法辨認所有動態行為。
- Runtime 外掛是在 ULCS Python 程序內載入的受信任程式碼。
- 污染標籤是治理資料，不會自動遮罩或淨化秘密。
- 輸出配額不等於程序記憶體、CPU、檔案大小或網路流量限制。
- 請勿直接執行不可信 `.sos` 或載入不可信外掛。

正式部署仍應搭配容器、低權限帳戶、檔案系統與網路隔離、OS 資源限制、外掛簽章及秘密管理。
