# ULCS v0.8 Provider Contract、Review Bundle 與 Approval Gate

版本：0.8  
狀態：實作規格

## 1. 目的

v0.8 將外部 AI／Agent 放在「提案者」而非「執行授權者」的位置。Provider 可以提出自然語言意圖、profile、bindings 與 preferences，但不能自行宣告 `ready`、不能提供能力政策、不能建立 Approval Record，也不能直接把候選內容送入 Approved Runner。

完整路徑：

```text
Provider Proposal
  → strict proposal validator
  → deterministic Intent Compiler
  → existing parser / DAG / contract / policy validators
  → Review Bundle digest
  → Approval Record
  → HMAC verification
  → Approved Runner
  → existing ULCS runtime
```

## 2. Provider Proposal

格式：`ULCS-Intent-Provider-Proposal`  
版本：`0.8`

必要欄位：

```json
{
  "format": "ULCS-Intent-Provider-Proposal",
  "version": "0.8",
  "provider": {
    "id": "provider-id",
    "model": "model-id"
  },
  "request": {
    "format": "ULCS-Intent-Request",
    "version": "0.7",
    "intent": "...",
    "profile": "log-analysis",
    "bindings": {},
    "preferences": {}
  }
}
```

Provider Proposal 採欄位白名單。下列欄位會直接拒絕：

- `workflow`
- `policy`
- `claims`
- `status`
- `ready`
- `approval`
- `signature`

Provider 的 `confidence` 只是一項來源聲明，不影響 ULCS 的 validator 結果，也不是安全證明。

## 3. Review Bundle

格式：`ULCS-Review-Bundle`  
版本：`0.8`

只有 Intent Bundle 狀態為 `ready` 時才能建立 Review Bundle。Review Bundle 固定包含並摘要以下檔案：

```text
provider-proposal.json
intent-plan.json
workflow.sos
artifact-contract.json
capability-policy.json
intent-bundle.json
```

每一項記錄：

- SHA-256
- byte size

整體 Review Bundle 再以 canonical JSON 建立一個 SHA-256 digest。`review-bundle.json` 本身不納入自己的摘要，避免循環依賴。

驗證時會拒絕：

- 缺少檔案
- 額外或不同的固定檔名集合
- 路徑分隔符或路徑逃逸
- 符號連結
- 檔案大小改變
- 檔案摘要改變
- Provider Proposal canonical digest 改變
- `intent-bundle.json` 不再是 `ready`

## 4. Approval Record

格式：`ULCS-Approval-Record`  
版本：`0.8`

Approval Record 綁定：

- Review Bundle digest
- `approve` 或 `reject`
- approver
- scopes
- reason
- issued_at
- algorithm

核准執行至少需要：

```json
{
  "decision": "approve",
  "scopes": ["execute"]
}
```

`reject`、缺少 `execute` scope、綁定其他 Bundle，或簽章無效都不可執行。

## 5. HMAC-SHA256

v0.8 使用 HMAC-SHA256，完全依賴 Python 標準函式庫。

這代表：

- 可證明 Approval Record 未被不知道共享密鑰的人修改。
- 不需要把密鑰寫入 Bundle 或 Approval Record。
- 可從環境變數或 key file 讀取密鑰。
- 命令列不接受明文 key。

但 HMAC **不是非對稱數位簽章**：

- 持有同一密鑰的任何一方都能建立有效記錄。
- 無法單靠 HMAC 對第三方證明是哪一位持鑰者簽署。
- key distribution、rotation、revocation 與檔案權限仍由部署環境負責。

因此 v0.8 文件稱它為「HMAC 完整性核准」，不把它描述成 PKI 或法律意義上的個人電子簽章。

最低 key 長度為 16 bytes；實務建議使用至少 32 個隨機 bytes。

## 6. Approved Runner

命令：`ulcs-approved`

Approved Runner 會：

1. 驗證 Review Bundle 與所有檔案。
2. 驗證 Approval Record HMAC。
3. 檢查 `decision=approve`。
4. 檢查 `execute` scope。
5. 將已核准的 workflow、policy 與 contract 複製到暫存快照。
6. 把快照交給既有 `ulcs` runtime。

下列治理參數禁止由 runtime arguments 覆寫：

- `--policy`
- `--contract`
- `--allow`
- `--deny`
- `--enforce-capabilities`
- `--plugin`
- `--cwd`
- `--db`

這可防止核准後以另一份 policy、contract、外掛或工作目錄替換治理邊界。

## 7. CLI

### 編譯 Provider Proposal

```bash
ulcs-provider examples/provider_proposal_v0.8.json \
  --output-dir output/v0.8 \
  --json
```

### 建立核准

```bash
export ULCS_APPROVAL_KEY='replace-with-a-random-secret'

ulcs-approve approve output/v0.8/review-bundle.json \
  --approver reviewer@example \
  --reason "Reviewed plan, policy, contract, and generated workflow." \
  --key-env ULCS_APPROVAL_KEY \
  --output output/v0.8/approval.json
```

### 驗證核准

```bash
ulcs-approve verify \
  output/v0.8/review-bundle.json \
  output/v0.8/approval.json \
  --key-env ULCS_APPROVAL_KEY
```

### 從核准快照執行

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

## 8. 邊界與未解問題

### 8.1 不是全域強制閘門

`ulcs-approved` 是受治理的執行入口；既有 `ulcs` CLI 為了相容性仍可直接執行 `.sos`。因此 v0.8 不是作業系統層的強制存取控制。部署環境若要求所有 Provider 產物都必須核准，應只授予 Agent 呼叫 `ulcs-approved` 的權限，並封鎖直接 `ulcs`、Python 模組與底層執行器。

### 8.2 外部輸入不一定在 Review digest 內

Review Bundle 摘要的是生成與治理檔案，不會自動封存 workflow 執行時讀取的外部日誌、資料庫、HTTP 回應或其他資料。Approval 表示核准這個計畫與治理邊界，不表示核准未來所有輸入內容。

### 8.3 TOCTOU

Approved Runner 會把 workflow、policy 與 contract 複製到驗證後的暫存快照，以降低核准檔案在驗證後被替換的風險。外部 runtime inputs 仍可能在執行期間改變；需要完全固定輸入時，應先將輸入納入 Artifact Store 或未來的 Input Contract。

### 8.4 Provider 身分

`provider.id` 與 `model` 是來源描述，不是密碼學身分。Provider attestation、公鑰簽章、透明日誌與撤銷機制留待後續版本。

## 9. 相容性

- `ULCS-Intent-Request` 與 `ULCS-Intent-Bundle` 維持 v0.7。
- LOG、Artifact Contract、Manifest 與 Checkpoint 維持 v0.6。
- `.sos` 語法與 Runtime Adapter API 不變。
- v0.1–v0.7 CLI 與工作流保持相容。
