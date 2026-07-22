# SOS 多語計算終端 MVP v0.1 驗證報告

**日期**：2026-07-21  
**對應理論**：SOS／ULCS 全語言組合基底  
**狀態**：第一輪概念驗證成功

## 一、驗證目標

本輪不嘗試完成「所有語言」，只驗證最核心命題：

> 多種原生語法能否保留自身形式，並在同一終端工作流中透過共同節點圖傳值與執行。

測試鏈：

```text
PowerShell → Regex → Python → SQLite
```

## 二、實際流程

1. `ps{}` 取得 `examples/logs/*.log`。
2. `regex{}` 從檔案內容擷取 `ERROR|FATAL`。
3. `py{}` 統計錯誤等級與來源。
4. `sql{}` 將 JSON 結果寫入 SQLite，並讀回最新紀錄。

## 三、結果

- 自動化測試：3/3 通過。
- 解析器可處理 Python 字典中的巢狀大括號。
- 型別鏈成功建立：

```text
None → FileList → MatchList → Json → Table
```

- 靜態副作用預覽成功辨識：
  - `filesystem.read`
  - `process.execute`
  - `python.execute`
  - `database.read`
  - `database.write`
- 實際資料結果：
  - `ERROR`：3
  - `FATAL`：1
  - 總匹配數：4
- SQLite 寫入與回讀成功。
- 成功輸出 Language Operator Graph JSON。

## 四、已證明的部分

### 4.1 語法保留可行

PowerShell、Regex、Python 與 SQL 不需要被改寫成一套最低公分母語法。共同層只負責標記語言邊界與資料依賴。

### 4.2 共同中介圖可行

四種語言可投影成共同節點：

```text
(language, AST/code, input type, output type, effects, runtime)
```

### 4.3 AI 不必位於重複執行路徑

`.sos` 與 LOG 一旦生成，後續執行由本地解析器及既有 Runtime 完成，不需要再次呼叫模型。

### 4.4 初步安全預覽可行

在執行前先列出語言、資料來源、輸入輸出型別、Runtime 與可能副作用，方向成立。

## 五、尚未證明的部分

- 任意 PowerShell 語法的穩定資料擷取。
- 真正安全的 Python／PowerShell 沙箱。
- 分支、循環、非同步與 DAG 排程。
- 複雜型別 schema 與零拷貝跨 Runtime 傳值。
- 上百或上千種語言適配器的擴展成本。
- 自然語言自動編譯後的穩定性與可重現性。

## 六、環境說明

本次驗證容器沒有安裝 `pwsh`，因此 `ps{}` 節點使用可攜式替代器執行以下子集：

```powershell
Get-ChildItem <path> -Filter <pattern> [-Recurse]
```

在 Windows 且存在 `powershell.exe` 或 `pwsh.exe` 時，程式會優先呼叫真正的 PowerShell Runtime。

因此，本輪已驗證「PowerShell 語法區塊的解析、節點化與資料橋接」，並以可攜式替代器完成端到端執行；仍應在使用者的 Windows 環境進行一次真正 PowerShell Runtime 驗證。

## 七、結論

第一輪 MVP 已足以支持以下判斷：

> ULCS／SOS 多語終端的核心概念可行。真正困難的部分不是讓不同語言出現在同一文件，而是建立可擴展的共同型別、能力權限、資料橋接與 Runtime 適配器標準。

下一輪最值得測試的是：

1. Windows 真正 PowerShell Runtime；
2. 分支型語言算子圖；
3. 自然語言編譯成固定 `.sos`；
4. 加入 Bash 或 JavaScript 作為第五語言；
5. 對節點輸入輸出加入 JSON Schema。
