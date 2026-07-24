# ULCS v0.9 — Input Contract、Ed25519 Attestation 與 Transparency Log 規格

版本：0.9.0  
狀態：Reference Implementation

## 1. 目標

ULCS v0.8 已能固定生成的 workflow、Artifact Contract、capability policy、Intent Plan 與 Provider Proposal，並以 HMAC Approval Record 控制受治理入口。

v0.9 補足四個仍未閉合的問題：

1. 外部輸入內容未必包含在 Review Bundle。
2. HMAC 的所有持有者都能生成相同類型的核准，無法區分簽署者。
3. Provider identity 只是文字描述，沒有 key possession 證明。
4. 核准、撤銷與發布事件缺少可驗證的順序紀錄。

v0.9 因此新增：

- `ULCS-Input-Contract`
- `ULCS-Input-Bundle`
- `ULCS-Provider-Attestation`
- `ULCS-Signed-Approval`
- `ULCS-Transparency-Log`
- `ULCS-Transparency-Checkpoint`
- `ulcs-input`
- `ulcs-sign`
- `ulcs-log`
- `ulcs-trusted`

v0.8 的 Review Bundle 與 HMAC Approved Runner 繼續保持相容。

## 2. 完整治理鏈

```text
Provider Proposal v0.8
        │
        ├─ deterministic Intent Compiler
        │       └─ Review Bundle v0.8
        │
        ├─ Provider Ed25519 private key
        │       └─ Provider Attestation v0.9
        │
Input Contract v0.9
        └─ capture exact bytes
                └─ Input Bundle v0.9

Review Bundle + Input Bundle + Provider Attestation
        │
        ├─ Reviewer Ed25519 private key
        │       └─ Signed Approval v0.9
        │
        ├─ provider-attested log event
        ├─ approval-issued log event
        └─ signed Transparency Checkpoint

ulcs-trusted
        ├─ verify Review Bundle
        ├─ verify Input Bundle
        ├─ verify Provider Attestation
        ├─ verify Signed Approval
        ├─ verify Transparency Log hash chain
        ├─ verify signed checkpoint
        ├─ reject revoked keys
        ├─ copy exact files into temporary snapshot
        └─ execute existing ULCS runtime
```

## 3. Input Contract

### 3.1 格式

```json
{
  "format": "ULCS-Input-Contract",
  "version": "0.9",
  "limits": {
    "max_file_bytes": 1048576,
    "max_total_bytes": 2097152
  },
  "entries": [
    {
      "name": "analysis-log",
      "kind": "file",
      "source": "input_data_v0.9/sample.log",
      "mount": "inputs/sample.log",
      "media_type": "text/plain; charset=utf-8"
    }
  ]
}
```

### 3.2 支援 kind

- `file`：從 Contract 所在目錄下的相對路徑捕獲 bytes。
- `inline-text`：將 UTF-8 字串捕獲為檔案。
- `inline-json`：將 canonical JSON bytes 捕獲為檔案。

### 3.3 路徑限制

- `source` 必須是相對路徑。
- 不接受 `..`、絕對路徑、磁碟代號或 URI。
- `source` 不得離開 Contract 根目錄。
- `source` 與 mount path 不得穿過符號連結。
- `mount` 必須位於 `inputs/` 下。
- entry name 與 mount 不可重複。

### 3.4 配額

- `max_file_bytes`：單一輸入的最大 bytes。
- `max_total_bytes`：整個 Input Bundle 的最大 bytes。
- 單檔限制不可大於總限制。

配額在 capture 與 verify 階段都會重查。

## 4. Input Bundle

Input Bundle 包含：

```json
{
  "format": "ULCS-Input-Bundle",
  "version": "0.9",
  "contract_digest": "...",
  "entries": [
    {
      "name": "analysis-log",
      "kind": "file",
      "mount": "inputs/sample.log",
      "media_type": "text/plain; charset=utf-8",
      "sha256": "...",
      "size": 44
    }
  ],
  "digest": "..."
}
```

`digest` 是不含自身 digest 欄位的 canonical JSON SHA-256。

驗證會重新檢查：

- `input-contract.json` canonical digest
- entry 集合
- kind 與 mount
- 每份掛載檔案的大小
- 每份掛載檔案的 SHA-256
- Contract 配額
- symlink 與路徑邊界

## 5. Provider Attestation

Provider Attestation 使用 Ed25519：

```json
{
  "format": "ULCS-Provider-Attestation",
  "version": "0.9",
  "algorithm": "ed25519",
  "proposal_digest": "...",
  "provider": {
    "id": "provider-name",
    "model": "model-description",
    "key_id": "sha256:..."
  },
  "issued_at": "2026-07-23T00:00:00+00:00",
  "signature": "base64url-ed25519-signature"
}
```

簽章覆蓋：

- Proposal digest
- Provider ID
- Model description
- Public-key fingerprint
- Issued timestamp

`key_id` 是 Ed25519 raw public key bytes 的 SHA-256。

驗證必須同時確認：

- Proposal digest 相同
- Provider ID 與 model 相同
- key ID 與 supplied public key 相同
- Ed25519 signature 正確

Provider Attestation 不授予執行權限。

## 6. Signed Approval

```json
{
  "format": "ULCS-Signed-Approval",
  "version": "0.9",
  "algorithm": "ed25519",
  "review_bundle_digest": "...",
  "input_bundle_digest": "...",
  "provider_attestation_digest": "...",
  "decision": "approve",
  "approver": {
    "id": "reviewer",
    "key_id": "sha256:..."
  },
  "scopes": ["execute"],
  "reason": "Reviewed exact workflow, policy, contract and inputs.",
  "issued_at": "2026-07-23T00:00:00+00:00",
  "signature": "base64url-ed25519-signature"
}
```

Signed Approval 同時固定三個物件：

- Review Bundle
- Input Bundle
- Provider Attestation

執行要求：

- `decision=approve`
- scopes 包含 `execute`
- 三個 digest 完全一致
- approver key ID 一致
- Ed25519 signature 正確

任何 workflow、policy、contract、Provider Proposal、輸入 byte 或簽章變更，都會使授權失效。

## 7. Transparency Log

### 7.1 Hash chain

每個 entry 包含：

- `sequence`
- `event`
- `subject`
- `issued_at`
- `previous_digest`
- `metadata`
- `digest`

entry digest 是前六項的 canonical JSON SHA-256。

每個 entry 的 `previous_digest` 必須等於前一個 entry digest；Log 的 `head_digest` 必須等於最後一筆 entry digest。

### 7.2 標準事件

v0.9 使用：

- `provider-attested`
- `approval-issued`
- `approval-rejected`
- `key-revoked`

`ulcs-trusted` 要求目前 Attestation 與 Approval 的 digest 都出現在 Log 中。

### 7.3 Key revocation

```text
key-revoked subject=<sha256:key-id>
```

若 Provider key、Approver key 或 Checkpoint signer key 在可信 checkpoint 所涵蓋的 Log 中被撤銷，Trusted Runner 會拒絕執行。

撤銷的有效性依賴使用者取得包含撤銷事件的最新可信 checkpoint。舊 checkpoint 不會神奇地知道未來發生的撤銷。

## 8. Transparency Checkpoint

Hash chain 本身只能證明內部一致，不能阻止整份本地檔案被重寫。

Checkpoint 以另一組 Ed25519 key 簽署：

- `log_head`
- `entry_count`
- signer ID
- signer key ID
- issued timestamp

Trusted Runner 必須驗證：

- Log chain 正確
- Log head 等於 checkpoint head
- entry count 相同
- checkpoint key ID 相同
- Ed25519 signature 正確
- checkpoint signer key 未撤銷

要取得 rollback protection，可信 checkpoint 必須被保存於攻擊者不能同步改寫的位置，例如：

- 另一個 repository
- append-only object storage
- release metadata
- 公開網站
- 多方 witness
- 獨立審計系統

## 9. Trusted Runner

`ulcs-trusted` 需要：

```text
Review Bundle
Input Bundle
Provider Attestation
Signed Approval
Provider public key
Approver public key
Transparency Log
Transparency Checkpoint
Checkpoint public key
```

通過後會建立暫存快照：

```text
workflow.sos
artifact-contract.json
capability-policy.json
provider-proposal.json
intent-plan.json
intent-bundle.json
review-bundle.json
input-contract.json
input-bundle.json
inputs/...
.ulcs-governance/...
```

Runtime `cwd` 被固定為該快照目錄。

Trusted Runner 會提供下列環境變數：

- `ULCS_INPUT_ROOT`
- `ULCS_INPUT_BUNDLE`
- `ULCS_REVIEW_BUNDLE_DIGEST`
- `ULCS_INPUT_BUNDLE_DIGEST`
- `ULCS_PROVIDER_ATTESTATION_DIGEST`
- `ULCS_SIGNED_APPROVAL_DIGEST`
- `ULCS_TRANSPARENCY_HEAD`

這些環境變數是可觀察 metadata，不會覆寫 policy 或自動授予能力。

與 v0.8 相同，Trusted Runner 禁止 runtime 端覆寫：

- policy
- contract
- allow／deny
- plugin
- cwd／db
- resource limits
- cache
- checkpoint／resume
- manifest verification

## 10. CLI

### 10.1 捕獲輸入

```bash
ulcs-input capture examples/input_contract_v0.9.json \
  --output-dir output/v0.9/input
```

### 10.2 產生 keypair

```bash
ulcs-sign keygen \
  --private-key provider-private.pem \
  --public-key provider-public.pem
```

### 10.3 Provider Attestation

```bash
ulcs-sign provider output/v0.9/review/provider-proposal.json \
  --private-key provider-private.pem \
  --output provider-attestation.json \
  --log transparency-log.json
```

### 10.4 Signed Approval

```bash
ulcs-sign approve \
  output/v0.9/review/review-bundle.json \
  output/v0.9/input/input-bundle.json \
  provider-attestation.json \
  --provider-public-key provider-public.pem \
  --private-key approver-private.pem \
  --approver reviewer \
  --output signed-approval.json \
  --log transparency-log.json
```

### 10.5 Signed checkpoint

```bash
ulcs-log checkpoint transparency-log.json \
  --signer log-operator \
  --private-key checkpoint-private.pem \
  --output transparency-checkpoint.json
```

### 10.6 Trusted execution

```bash
ulcs-trusted \
  output/v0.9/review/review-bundle.json \
  output/v0.9/input/input-bundle.json \
  provider-attestation.json \
  signed-approval.json \
  --provider-public-key provider-public.pem \
  --approver-public-key approver-public.pem \
  --transparency-log transparency-log.json \
  --log-checkpoint transparency-checkpoint.json \
  --checkpoint-public-key checkpoint-public.pem \
  -- --yes --json
```

## 11. 安全邊界

### 11.1 Ed25519 不是身分註冊系統

正確簽章只證明簽署者持有對應 private key。它不自動證明：

- Provider 是哪一家公司或哪一個人
- Model description 真實
- Approver 具有法律授權
- Key 未被竊取

現實身分與 key 的綁定仍需 PKI、組織目錄、硬體金鑰、多方見證或其他治理系統。

### 11.2 Keygen private key

Reference CLI 產生未加密 PKCS8 PEM，並盡可能設定 owner-only file mode。Windows ACL、備份、同步工具與秘密外洩仍由部署環境負責。

正式環境應優先使用：

- HSM
- TPM
- OS key store
- cloud KMS
- secret manager
- hardware token

### 11.3 Transparency Log 不是不可變帳本

本地 JSON hash chain 是 tamper-evident 結構，不是共識網路、區塊鏈或作業系統 append-only storage。

攻擊者若能同時重寫 Log 與所有可信 checkpoint，仍可重建一條新的合法 hash chain。

### 11.4 Input 範圍

v0.9 Reference Implementation 固定：

- local file bytes
- inline UTF-8 text
- inline canonical JSON

尚未固定：

- live HTTP response
- database query snapshot
- message queue
- stdin stream
- secret manager value
- device input
- nondeterministic clock／randomness

這些來源未被宣稱為已解決。

### 11.5 Multi-writer

Transparency Log 使用 atomic replacement，適合單一 writer。v0.9 未提供跨程序分散式鎖或多 writer 共識。

### 11.6 OS isolation

Signed governance 不等於作業系統 sandbox。正式部署仍需：

- 低權限帳戶
- container／VM
- filesystem isolation
- network egress policy
- process limits
- secret isolation
- audit retention

## 12. 相容性

- `.sos` 語法不變。
- Runtime Adapter API 不變。
- Review Bundle 維持 v0.8。
- HMAC Approval Record 維持 v0.8。
- LOG、Manifest、Artifact 與 Checkpoint 維持 v0.6。
- Intent Request／Intent Bundle 維持 v0.7。
- `ulcs-approved` 繼續處理 v0.8 HMAC。
- `ulcs-trusted` 處理 v0.9 Ed25519 與 Input Bundle 鏈。
