# ULCS Intent Compiler Specification v0.7

## 1. 目的

Intent Compiler 將受支援的自然語言意圖轉換為**可審查、可拒絕、可驗證**的 ULCS 工作流 bundle。它不是直接執行自然語言，也不是把模型輸出視為可信程式碼。

核心順序：

```text
Intent Request
  → profile resolution
  → Intent Plan
  → generated .sos / contract / policy
  → existing ULCS parser and validators
  → ready | needs_clarification | rejected
```

只有 `ready` bundle 才代表產物已通過現有 parser、DAG、Artifact Contract 與 capability policy 驗證。即使是 `ready`，仍需由使用者或上層 Agent 審查後另行執行。

## 2. Intent Request

```json
{
  "format": "ULCS-Intent-Request",
  "version": "0.7",
  "intent": "分析以下日誌，找出 ERROR 與 FATAL，統計各類數量。",
  "profile": "log-analysis",
  "bindings": {
    "text": "ERROR one\nFATAL two",
    "terms": ["ERROR", "FATAL"]
  },
  "preferences": {
    "include_matches": true,
    "persist_summary": true
  }
}
```

- `intent`：人類原始意圖，不可為空。
- `profile`：可省略；若省略，編譯器只能在有明確訊號時解析。
- `bindings`：具體值，優先於自然語言抽取。
- `preferences`：非必要的輸出與持久化偏好。

Bindings 不是秘密管理機制。v0.7 不應在 request 中放入 API key、密碼或長期憑證。

## 3. 狀態

### `ready`

生成的 workflow、Artifact Contract 與 capability policy 已通過既有 ULCS 驗證鏈。

### `needs_clarification`

缺少來源、匹配詞、URL、受支援的 HTTP method 或 profile。此狀態不生成可執行 `.sos`。

### `rejected`

編譯器生成了候選產物，但候選產物未通過 parser、graph、contract 或 policy 驗證。此狀態不可執行。

## 4. v0.7 Profiles

### 4.1 `log-analysis`

支援：

- `bindings.text` 內嵌文字；或
- `bindings.source_path` 搭配 `pattern` 的本地檔案來源；
- 明確 `terms`；
- 逐行 regex 抽取；
- 分類計數；
- 可選保留匹配明細；
- summary Artifact Contract。

生成圖通常為：

```text
py or ps source
  → regex matches
  → py summary
```

v0.7 的可攜式 PowerShell 子集不可靠處理含空白路徑；遇到這類來源會回傳 `needs_clarification`，而不是生成表面可讀但跨平台不穩定的程式。

### 4.2 `http-json-fetch`

支援：

- 明確的 `http`／`https` URL；
- GET 或 HEAD；
- HTTP response 投影成 `status`、`url`、`body`；
- origin-scoped `network.access` claim；
- response/result schema。

v0.7 不自動生成 POST、PUT、PATCH、DELETE、Authorization header、secret injection 或任意遠端寫入。

## 5. Generated Bundle

一個 `ready` bundle 會原子寫出：

```text
intent-plan.json
workflow.sos
artifact-contract.json
capability-policy.json
intent-bundle.json
```

### `intent-plan.json`

保存：

- 原始 request；
- profile；
- confidence；
- assumptions；
- missing fields；
- risks；
- structured steps；
- validator 結果；
- program／plan／LOG digest。

### `workflow.sos`

普通 ULCS `.sos` 文件。Intent Compiler 不新增第二套執行語言。

### `artifact-contract.json`

使用 v0.6 Artifact Contract 格式。v0.7 Intent Compiler 是 Artifact Contract 的產生者，不改變其 runtime 語義。

### `capability-policy.json`

預設為 `enforce`，只允許生成圖實際推導出的 exact claims，並對未使用的高風險能力加入顯式 deny。

### `intent-bundle.json`

保存 bundle metadata 與相對檔名，不重複嵌入完整 workflow、contract 與 policy，避免同一產物存在多份可能漂移的副本。

## 6. Validation Pipeline

候選產物必須依序通過：

```text
parse_text(workflow)
ArtifactContracts.from_mapping(contract).apply(program)
enrich_and_validate(program)
CapabilityPolicy(mode="enforce").check_program(program)
program_digest / plan_digest / LOG digest
```

任何階段失敗都不得標記為 `ready`。

## 7. 信心分數

`confidence` 只描述 profile 與 bindings 的確定程度，不描述程式正確率，也不是安全證明。

- 明確 profile、URL、terms、source bindings：較高。
- 由自然語言抽取 extension、path 或 terms：較低。
- 缺欄位：固定為 0，狀態為 `needs_clarification`。
- validator 拒絕：固定為 0，狀態為 `rejected`。

## 8. 安全邊界

- Intent Compiler 不自動執行 bundle。
- `ready` 表示通過 ULCS 靜態與結構驗證，不表示業務意圖必然正確。
- 自然語言中的資料、URL、路徑與 terms 仍是不可信輸入。
- 生成的 Python、PowerShell、Regex 與 HTTP 節點仍受既有 Runtime 安全邊界限制。
- capability policy 是啟動閘門，不是 OS sandbox。
- schema 是受限 JSON Schema 子集。
- Artifact Store 仍未加密與簽章。
- Intent Request 與 Bundle 不應承載秘密。

## 9. CLI

```bash
ulcs-intent examples/intent_request_v0.7.json \
  --output-dir output/v0.7
```

直接文字與 bindings：

```bash
ulcs-intent \
  --text "分析以下日誌，找出 ERROR 與 FATAL" \
  --profile log-analysis \
  --binding 'text="ERROR one\nFATAL two"' \
  --binding 'terms=["ERROR","FATAL"]' \
  --output-dir output/v0.7
```

狀態碼：

```text
0  ready
2  CLI 參數錯誤
3  request／編譯器格式錯誤
4  needs_clarification
5  rejected
```

## 10. 後續方向

v0.7 刻意不使用外部 LLM。後續模型型 Intent Adapter 必須輸出相同 `Intent Request`／`Intent Plan` 結構，並接受相同 deterministic validator；模型不能直接繞過 bundle、policy 或 review gate。
