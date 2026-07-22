# ULCS — Universal Language Composition Substrate

ULCS（全語言組合基底）不是把所有程式語言改寫成單一語法，而是讓不同語言保留自身語義，並在同一個可型別化、可驗證的 Language Operator Graph 中組合。

目前版本：**v0.2.0**

## v0.2 已完成

- `ps{}`：PowerShell／可攜式檔案列舉子集
- `regex{}`：正則表達式
- `py{}`：隔離 Python 子程序
- `sql{}`：SQLite
- 單一與多輸入節點
- 前向引用
- DAG 循環與引用驗證
- 穩定拓樸排序
- LOG v0.2 中介表示
- Runtime 適配器註冊表
- 型別與副作用安全預覽
- Windows／Ubuntu CI

## 安裝與測試

```bash
python -m pip install -e .
python -m unittest discover -s tests -v
```

## 執行線性範例

```bash
ulcs examples/error_report.sos --dry-run --emit-ir output/error-report.json
ulcs examples/error_report.sos --yes --json
```

舊命令仍可使用：

```bash
sos-mvp examples/error_report.sos --dry-run
```

## DAG 分支匯流範例

```sos
source errors = py{
result = {"ERROR": 3, "FATAL": 1}
}

source metadata = py{
result = {"environment": "demo", "version": "0.2"}
}

transform report = py{
result = {
    "summary": input["counts"],
    "metadata": input["meta"],
    "total": sum(input["counts"].values()),
}
} from errors as counts, metadata as meta
```

執行：

```bash
ulcs examples/dag_merge.sos --yes --json
```

## `.sos` 語法

```text
<role> <node-id> = <language>{
    <原生語言內容>
} [from <node>[.<field>] [as <name>], ...]
```

目前角色是描述性標籤：

- `source`
- `extract`
- `transform`
- `store`
- `run`

單一輸入會直接傳給 Runtime。多輸入會形成具名映射；預設鍵是來源節點名稱，也可以用 `as` 指定。

## LOG v0.2

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
- runtime

詳細規格見 [`docs/LOG_SPEC_v0.2.md`](docs/LOG_SPEC_v0.2.md)。

## 新增語言

v0.2 將語言能力抽象為 `LanguageAdapter`。每個適配器宣告：

- 語言名稱與別名
- 可接受輸入型別
- 輸出型別
- Runtime
- 副作用分析
- 執行方法

因此新增語言不需要修改 `.sos` 解析器或 LOG 資料模型。

## 安全邊界

這仍是研究型參考 Runtime，不是安全沙箱：

- Python 子程序仍可能存取主機資源。
- PowerShell 具有啟動程序的作業系統權限。
- 副作用偵測是靜態提示，不是完整保證。
- 請勿執行不可信 `.sos` 文件。

正式安全層仍需加入能力權限、容器／沙箱、網路隔離、資源限制、簽章與跨節點污染追蹤。

## 下一階段

v0.3 的優先項目：

1. 能力權限宣告與拒絕策略
2. Runtime 外掛載入
3. HTTP、Bash、JavaScript 與 jq 適配器
4. 分層平行 DAG 排程
5. 自然語言編譯為固定 `.sos` 與 LOG
