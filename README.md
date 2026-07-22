# ULCS — Universal Language Composition Substrate

ULCS（全語言組合基底）不是把所有程式語言改寫成單一語法，而是讓不同語言保留自身語義，並在同一個可型別化、可驗證、可套用能力政策的 Language Operator Graph 中組合。

目前版本：**v0.3.0**

## v0.3 已完成

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
- 多 sink 輸出
- LOG v0.3 中介表示

### 安全與擴充

- audit／enforce 能力政策
- `allow`／`deny` wildcard
- 完整 DAG 執行前拒絕
- Python entry point Runtime 外掛
- `--plugin module` 動態外掛
- HTTP 本機／私有位址拒絕與 2 MiB 回應限制
- Windows／Ubuntu、Python 3.11–3.13 CI

## 安裝與測試

```bash
python -m pip install -e .
python -m unittest discover -s tests -v
```

列出語言與能力：

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

## 線性跨語言範例

```bash
ulcs examples/error_report.sos --dry-run --emit-ir output/error-report.json
ulcs examples/error_report.sos --yes --json
```

流程：

```text
PowerShell → Regex → Python → SQLite
```

## DAG 分支匯流範例

```sos
source errors = py{
result = {"ERROR": 3, "FATAL": 1}
}

source metadata = py{
result = {"environment": "demo", "version": "0.3"}
}

transform report = py{
result = {
    "summary": input["counts"],
    "metadata": input["meta"],
    "total": sum(input["counts"].values()),
}
} from errors as counts, metadata as meta
```

```bash
ulcs examples/dag_merge.sos --yes --json
```

## v0.3 四 Runtime 範例

```text
Python → jq → JavaScript → Bash
```

先做政策與 Runtime 預覽：

```bash
ulcs examples/polyglot_v0.3.sos \
  --policy examples/capability_policy_v0.3.json \
  --dry-run \
  --emit-ir output/polyglot-v0.3.json
```

環境具備 Bash、Node.js 與 jq 時可執行：

```bash
ulcs examples/polyglot_v0.3.sos \
  --policy examples/capability_policy_v0.3.json \
  --yes --json
```

## 能力政策

未指定政策時使用 `audit`，以保持 v0.2 執行相容性。未列出的能力會顯示為 `AUDIT`，但明確 `deny` 仍會阻止執行。

嚴格模式：

```bash
ulcs examples/error_report.sos \
  --enforce-capabilities \
  --allow filesystem.read \
  --allow process.execute \
  --allow python.execute \
  --allow database.* \
  --yes
```

政策檔：

```json
{
  "mode": "enforce",
  "allow": ["python.execute", "process.execute"],
  "deny": ["network.*", "filesystem.delete"]
}
```

判定優先序：

1. `deny`
2. `allow`
3. enforce 模式下的未授權能力
4. audit 模式下的稽核提示

完整規格見 [`docs/CAPABILITY_POLICY_v0.3.md`](docs/CAPABILITY_POLICY_v0.3.md)。

## LOG v0.3

```bash
ulcs examples/dag_merge.sos --dry-run --emit-ir output/dag.json
```

LOG 包含：

- nodes
- edges
- execution order
- sinks
- input/output types
- effects
- capabilities
- runtime

詳細規格見：

- [`docs/LOG_SPEC_v0.3.md`](docs/LOG_SPEC_v0.3.md)
- [`docs/LOG_SPEC_v0.2.md`](docs/LOG_SPEC_v0.2.md)

## Runtime 外掛

第三方套件可使用 Python entry point：

```toml
[project.entry-points."ulcs.adapters"]
example = "my_ulcs_plugin:ExampleAdapter"
```

也可以直接載入模組：

```bash
ulcs workflow.sos --plugin my_ulcs_plugin --dry-run
```

模組必須提供：

```python
ULCS_ADAPTERS = [ExampleAdapter()]
```

或：

```python
def register():
    return [ExampleAdapter()]
```

完整契約見 [`docs/RUNTIME_PLUGIN_SPEC_v0.3.md`](docs/RUNTIME_PLUGIN_SPEC_v0.3.md)。

## HTTP 適配器

可使用純 URL：

```sos
source remote = http{
https://example.com/data.json
}
```

或 JSON request specification：

```sos
source remote = http{
{
  "url": "https://api.example.com/data",
  "method": "GET",
  "headers": {"Accept": "application/json"}
}
}
```

HTTP 適配器：

- 只接受 HTTP／HTTPS
- 拒絕 localhost、`.local` 與解析後的非公網 IP
- 重新驗證 redirect 目標
- 回應限制為 2 MiB
- 可用 `ULCS_HTTP_ALLOW_HOSTS` 設定主機 wildcard allowlist

仍需先由能力政策允許 `network.access`。

## 安全邊界

v0.3 已能在 Runtime 啟動前拒絕未授權能力，但仍不是作業系統級安全沙箱：

- Python、JavaScript、Bash 與 PowerShell 仍具有啟動程序帳戶的權限。
- 靜態能力推斷可能無法辨認所有動態行為。
- Runtime 外掛是在 ULCS Python 程序內載入的受信任程式碼。
- HTTP 防護降低常見 SSRF 風險，但不能取代完整網路隔離與代理政策。
- 請勿執行不可信 `.sos` 或載入不可信外掛。

正式部署仍應搭配容器、低權限帳戶、檔案系統與網路隔離、資源限制、簽章及資料污染追蹤。

## 下一階段

v0.4 的優先項目：

1. capability resource scope：目錄、網域、資料庫與命令白名單
2. 分層平行 DAG 排程與 deterministic replay
3. Runtime 資源配額與取消機制
4. 敏感資料標籤與跨節點污染追蹤
5. 自然語言編譯為固定 `.sos`、LOG 與政策草案
