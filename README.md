# ULCS — SOS Polyglot Computational Terminal MVP v0.1

這是「全語言組合基底（ULCS）」的第一個可執行概念驗證。它不是把所有語言改寫成同一套語法，而是在同一份 `.sos` 文件中保留並組合不同語言。


## 專案定位

本倉庫是 **Universal Language Composition Substrate（ULCS，全語言組合基底）** 的主要開發倉庫。
目前程式碼為第一個 SOS 多語計算終端 MVP；後續規格、Runtime 適配器、共同型別、Language Operator Graph 與 AI 編譯層均在此演進。

目前支援：

- `ps{}`：PowerShell；若系統沒有 `pwsh`，MVP 會對 `Get-ChildItem <path> -Filter <pattern> [-Recurse]` 使用可攜式替代器。
- `regex{}`：Python `re` 模式匹配。
- `py{}`：隔離子程序中的 Python 區塊；以 `input` 取得上游資料，必須將輸出指定給 `result`。
- `sql{}`：SQLite；上游字典欄位可直接作為具名參數，例如 `:payload`。

## 立即執行

Windows PowerShell：

```powershell
python -m sos_mvp examples/error_report.sos --dry-run --emit-ir output/language_operator_graph.json
python -m sos_mvp examples/error_report.sos --yes --json
```

Linux／macOS：

```bash
python -m sos_mvp examples/error_report.sos --dry-run --emit-ir output/language_operator_graph.json
python -m sos_mvp examples/error_report.sos --yes --json
```

## `.sos` 範例

```sos
source logs = ps{
    Get-ChildItem ./logs -Filter *.log
}

extract errors = regex{
    ERROR|FATAL
} from logs

transform report = py{
result = {"payload": {"total_matches": len(input)}}
} from errors

store saved = sql{
CREATE TABLE IF NOT EXISTS reports(payload TEXT);
INSERT INTO reports(payload) VALUES (:payload);
SELECT payload FROM reports ORDER BY rowid DESC LIMIT 1;
} from report
```

## 語法

```text
<role> <node-id> = <language>{
    <原生語言內容>
} [from <upstream-node>[.<field>]]
```

角色目前只是描述性標籤：`source`、`extract`、`transform`、`store`、`run`。

## 執行模型

1. 解析 `.sos` 語言區塊。
2. 產生 Language Operator Graph（LOG）。
3. 推斷輸入／輸出型別與副作用。
4. 顯示安全預覽。
5. 依序調用各 Runtime。
6. 以 JSON 相容資料在節點間傳值。

## 安全邊界

這是概念驗證，不是安全沙箱：

- `py{}` 雖在獨立 Python 子程序執行，但仍可能讀寫主機可存取的檔案。
- 真正的 PowerShell 區塊具有該程序的作業系統權限。
- 副作用偵測目前是靜態關鍵字掃描，只能作為預覽，不能視為完整安全保證。
- 請勿執行不可信 `.sos` 文件。

正式版本應加入容器沙箱、細粒度檔案系統能力、網路隔離、資源限制、簽章與完整資料流污染追蹤。

## 測試

```bash
python -m unittest discover -s tests -v
```

## 下一步

- 分支與 DAG 執行，而不只線性流程。
- 明確共同型別 schema。
- JavaScript、Bash、HTTP、jq、Lean 等語言適配器。
- AI 將自然語言編譯為固定 `.sos` 與 LOG，而非每次重新推理。
- 真正的能力權限與跨節點敏感資料追蹤。
