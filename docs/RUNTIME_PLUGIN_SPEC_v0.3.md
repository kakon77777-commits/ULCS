# ULCS Runtime 外掛規格 v0.3

## 1. 目的

ULCS 不應把上百、上千種語言永久硬編碼在核心。v0.3 將 Runtime 擴充分成：

- 核心適配器：PowerShell、Regex、Python、SQLite
- 內建擴充：Bash、JavaScript、jq、HTTP
- Python 套件 entry point
- 命令列或環境變數載入的模組外掛

新增語言不需要修改 `.sos` 解析器或 LOG 資料模型。

## 2. LanguageAdapter 契約

每個適配器必須繼承 `LanguageAdapter` 並提供：

```python
class ExampleAdapter(LanguageAdapter):
    language = "example"
    aliases = ("ex",)
    accepted_input_types = frozenset({"None", "Json", "Any"})
    output_type = "Json"

    def runtime(self) -> str:
        ...

    def effects(self, code: str) -> list[str]:
        ...

    def execute(self, code, input_value, context):
        ...
```

### 2.1 language

LOG 中的 canonical language name。

### 2.2 aliases

可選的表面語法別名。別名不可與既有適配器衝突。

### 2.3 accepted_input_types

適配器可接受的 ULCS 邊界型別集合。

### 2.4 output_type

節點輸出的 ULCS 邊界型別。

### 2.5 runtime()

回報實際 Runtime。找不到外部執行檔時，應回報 `unavailable:<name>`，並在真正執行時拋出 `ExecutionError`。

### 2.6 effects(code)

回傳節點需要的能力。此結果會進入 LOG 與能力政策判定。

### 2.7 execute(...)

執行原生語言內容。輸入與輸出應盡量保持 JSON 可序列化。

## 3. 套件 entry point

第三方 Python 套件可在自己的 `pyproject.toml` 宣告：

```toml
[project.entry-points."ulcs.adapters"]
example = "my_ulcs_plugin:ExampleAdapter"
```

entry point 可載入：

- `LanguageAdapter` instance
- `LanguageAdapter` subclass
- 回傳適配器的 callable
- 適配器 iterable

ULCS 啟動時會載入 `ulcs.adapters` 群組。

## 4. 模組外掛

### 4.1 ULCS_ADAPTERS

```python
ULCS_ADAPTERS = [ExampleAdapter()]
```

執行：

```bash
ulcs workflow.sos --plugin my_ulcs_plugin --dry-run
```

### 4.2 register()

```python
def register():
    return [ExampleAdapter()]
```

`register()` 也可自行完成註冊並回傳 `None`。

### 4.3 環境變數

```bash
export ULCS_ADAPTER_MODULES=my_plugin,another_plugin
```

## 5. v0.3 內建擴充

### Bash

- 語言：`bash`、`sh`
- 輸入同時寫入 stdin 與 `ULCS_INPUT`
- stdout 若為合法 JSON 會轉為 JSON，否則保留文字

### JavaScript

- 語言：`js`、`javascript`、`node`
- Node.js 子程序
- 以 `input` 取得資料，將輸出指定給 `result`

### jq

- 語言：`jq`
- 將上游資料序列化為 JSON 後交給 jq filter

### HTTP

- 語言：`http`、`https`
- 接受純 URL 或 JSON request specification
- 限制回應為 2 MiB
- 拒絕 localhost、`.local` 與解析後的非公網 IP
- 可用 `ULCS_HTTP_ALLOW_HOSTS` 再限制主機 wildcard

HTTP JSON 範例：

```sos
source remote = http{
{
  "url": "https://api.example.com/data",
  "method": "GET",
  "headers": {"Accept": "application/json"}
}
}
```

## 6. 安全責任

外掛程式碼本身在 ULCS Python 程序內載入，因此外掛是受信任程式碼，不是沙箱內容。安裝或載入不可信外掛，等同執行不可信 Python 套件。

正式生產環境至少應：

- 固定外掛版本與雜湊
- 使用簽章或可信套件來源
- 在低權限帳戶或容器內執行 ULCS
- 對外部 Runtime 設定 CPU、記憶體、時間與網路限制
- 將能力政策與作業系統強制控制結合
