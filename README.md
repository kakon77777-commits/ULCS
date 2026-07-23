# ULCS — Universal Language Composition Substrate

ULCS（全語言組合基底）不是把所有程式語言改寫成單一語法，而是讓不同語言保留自身語義，並在同一個可型別化、可驗證、可治理、可重現、可恢復、可由意圖編譯與核准的 Language Operator Graph 中組合。

目前版本：**v0.8.0**

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

### Intent 與核准鏈

- v0.7 `Intent Request` 與 review-first deterministic Intent Compiler
- `ready`／`needs_clarification`／`rejected`
- 生成 `.sos`、Artifact Contract、enforce policy 與 bundle metadata
- v0.8 外部 AI／Agent Provider Contract
- 固定檔案集合、逐檔 SHA-256 與 canonical Review Bundle digest
- `approve`／`reject` Approval Record
- `execute` scope
- HMAC-SHA256 完整性核准
- Approved Runner 驗證後快照執行
- 核准後禁止覆寫 policy、contract、plugin、cwd 與能力規則

## 安裝與測試

```bash
python -m pip install -e .
python -m unittest discover -s tests -v
```

主要命令：

```bash
ulcs --list-languages
ulcs --list-capabilities
ulcs-intent --list-profiles
ulcs-provider --help
ulcs-approve --help
ulcs-approved --help
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

## v0.8 Provider → Approval → Execution

### 1. Provider Proposal

外部 AI／Agent 只能提交意圖資料：

```json
{
  "format": "ULCS-Intent-Provider-Proposal",
  "version": "0.8",
  "provider": {
    "id": "example-provider",
    "model": "review-only-fixture"
  },
  "request": {
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
}
```

Provider 不得自行提交 `workflow`、`policy`、`claims`、`status`、`ready`、`approval` 或 `signature`。

### 2. 編譯與建立 Review Bundle

```bash
ulcs-provider examples/provider_proposal_v0.8.json \
  --output-dir output/v0.8 \
  --json
```

只有底層 Intent Bundle 真正通過既有 parser、DAG、Artifact Contract 與 capability policy validator，才會產生：

```text
output/v0.8/
  provider-proposal.json
  intent-plan.json
  workflow.sos
  artifact-contract.json
  capability-policy.json
  intent-bundle.json
  review-bundle.json
```

Review Bundle 綁定前六份檔案的 SHA-256 與 byte size，再建立整體 canonical digest。

### 3. 建立 Approval Record

```bash
export ULCS_APPROVAL_KEY='replace-with-a-random-secret'

ulcs-approve approve output/v0.8/review-bundle.json \
  --approver reviewer@example \
  --reason "Reviewed plan, policy, contract, and workflow." \
  --key-env ULCS_APPROVAL_KEY \
  --output output/v0.8/approval.json
```

命令列不接受明文 key。可使用環境變數或 key file。

### 4. 驗證

```bash
ulcs-approve verify \
  output/v0.8/review-bundle.json \
  output/v0.8/approval.json \
  --key-env ULCS_APPROVAL_KEY
```

以下任一情況會拒絕：

- 任何 reviewed file 缺失、被替換或 byte size 改變
- Review Bundle digest 不一致
- Approval 綁定另一個 Bundle
- HMAC 不正確
- `decision=reject`
- 缺少 `execute` scope

### 5. Approved Runner

```bash
ulcs-approved \
  output/v0.8/review-bundle.json \
  output/v0.8/approval.json \
  --key-env ULCS_APPROVAL_KEY \
  -- \
  --artifact-mode all \
  --artifact-dir output/v0.8/artifacts \
  --yes --json
```

Approved Runner 會將核准的 workflow、policy 與 contract 複製到驗證後的暫存快照，再交給既有 Runtime。

完整規格見 [`docs/PROVIDER_APPROVAL_SPEC_v0.8.md`](docs/PROVIDER_APPROVAL_SPEC_v0.8.md)。

## v0.7 Intent Compiler

直接編譯 Intent Request：

```bash
ulcs-intent examples/intent_request_v0.7.json \
  --output-dir output/v0.7
```

支援 profiles：

- `log-analysis`：內嵌文字或本地檔案、明確 terms、逐行 regex 抽取與分類統計
- `http-json-fetch`：明確 URL 的 GET／HEAD 與 origin-scoped `network.access`

Intent Compiler不直接執行生成結果。完整規格見 [`docs/INTENT_COMPILER_SPEC_v0.7.md`](docs/INTENT_COMPILER_SPEC_v0.7.md)。

## Artifact Contract、Store 與 Resume

Contract 使用獨立 JSON 文件套用到既有節點，因此 `.sos` 表面語法維持穩定。

節點可宣告：

```text
input_schema
output_schema
persist
```

相關規格：

- [`docs/ARTIFACT_CONTRACT_v0.6.md`](docs/ARTIFACT_CONTRACT_v0.6.md)
- [`docs/ARTIFACT_SPEC_v0.6.md`](docs/ARTIFACT_SPEC_v0.6.md)
- [`docs/CHECKPOINT_RESUME_v0.6.md`](docs/CHECKPOINT_RESUME_v0.6.md)

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

Provider Approval 流程中的 `ulcs-approved` 禁止追加 `--plugin`，避免核准後注入未審查 Python 程式碼。

## 版本演化

```text
v0.1  跨語言線性管線
v0.2  Language Operator Graph
v0.3  能力政策與 Runtime 外掛
v0.4  資源範圍、平行 DAG、配額、污染追蹤
v0.5  內容定址快取、Manifest、重放驗證
v0.6  Artifact Contract、schema、checkpoint、resume
v0.7  Intent Request、review-first compiler、生成 bundle
v0.8  Provider Contract、Review Bundle、Approval Gate、HMAC 核准
```

## 安全邊界

ULCS v0.8 仍是參考執行層，不是作業系統級沙箱，也不是任意自然語言 AGI 執行器：

- `ready` 只表示通過 ULCS validator，不表示業務意圖必然正確。
- Provider 身分欄位是來源描述，不是密碼學身分。
- HMAC 是共享密鑰完整性驗證，不是非對稱公鑰簽章或第三方不可否認證明。
- `ulcs-approved` 是受治理入口；為了相容性，既有 `ulcs` 仍能直接執行 `.sos`。
- 要求全域強制核准時，部署環境必須封鎖 Agent 直接呼叫 `ulcs`、Python 模組與底層執行器。
- Review digest 綁定生成與治理檔案，不自動封存未來的日誌、資料庫、HTTP 回應或其他外部輸入。
- Python、JavaScript、Bash 與 PowerShell 仍具有啟動程序帳戶的權限。
- 靜態能力與資源推斷無法辨認所有動態行為。
- Artifact Store、cache 與 checkpoint 尚未加密。
- Checkpoint 不是交易回滾，也不會撤銷已發生的外部副作用。
- Intent Request、Provider Proposal 與 Bundle 不應承載秘密。
- 請勿直接執行不可信 `.sos`、contract、policy 或 Runtime 外掛。

正式部署仍應搭配低權限帳戶、容器、檔案系統與網路隔離、OS 資源限制、秘密管理、key rotation、Artifact 加密與生命週期政策。
