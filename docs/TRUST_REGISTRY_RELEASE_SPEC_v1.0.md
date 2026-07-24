# ULCS Trust Registry、Threshold Governance 與 Governed Release Bundle v1.0

狀態：Reference Specification  
版本：1.0  
日期：2026-07-24

## 1. 目的

ULCS v0.9 已能固定工作流程、能力政策、Artifact Contract、輸入 bytes、Provider Attestation、單一 Reviewer Approval、Transparency Log 與 signed checkpoint。

但 v0.9 的 Trusted Runner 仍由命令列分別接收 Provider、Approver 與 Checkpoint public key。這代表「哪些 key 應被信任」仍由每一次呼叫者決定，而且單一 Reviewer key 可以獨立核准執行。

v1.0 引入四個治理層：

1. Root-signed Trust Registry。
2. M-of-N Threshold Approval Set。
3. 獨立 Checkpoint Witness Set。
4. Self-contained Governed Release Bundle。

目標不是宣稱建立全球 PKI，而是提供一個可攜、可重現、可稽核、可在離線環境驗證的 reference governance chain。

## 2. 信任模型

```text
External Root Public Key
        │
        ▼
Root-signed Trust Registry
        │
        ├── Provider keys
        ├── Approver keys
        ├── Checkpoint keys
        ├── Witness keys
        └── Release keys
```

執行者預先信任的唯一密碼學 anchor 是 Root public key。

Trust Registry 內嵌 operational public keys、principal、role 與 status。Provider、Approver、Checkpoint、Witness 與 Release signature 必須同時滿足：

- key ID 與實際 Ed25519 public key 相符；
- key 存在於已由 Root key 簽署的 Registry；
- key status 是 `active`；
- key 擁有該次用途需要的 role；
- signature 中的 principal 與 Registry principal 一致。

Root private key 不參與日常執行，正式部署應離線保存。

## 3. Trust Registry

格式：`ULCS-Trust-Registry`  
版本：`1.0`  
演算法：`ed25519`

### 3.1 頂層欄位

```json
{
  "format": "ULCS-Trust-Registry",
  "version": "1.0",
  "algorithm": "ed25519",
  "registry_id": "production-registry-2026-07",
  "issued_at": "2026-07-24T00:00:00+00:00",
  "root": {
    "key_id": "sha256:<root-public-key-fingerprint>"
  },
  "policy": {},
  "keys": [],
  "signature": "<base64url-ed25519-signature>"
}
```

Root signature 覆蓋除 `signature` 外的 canonical JSON。

Registry digest 是包含 signature 的完整 Registry canonical SHA-256。

### 3.2 Registry key entry

```json
{
  "principal": "reviewer-a",
  "key_id": "sha256:<public-key-fingerprint>",
  "roles": ["approver"],
  "status": "active",
  "public_key": "-----BEGIN PUBLIC KEY-----\n..."
}
```

允許 roles：

- `provider`
- `approver`
- `checkpoint`
- `witness`
- `release`

同一把 key 可以擁有多個 role，但同一 key ID 不得在 Registry 內重複出現，也不得對應不同 principal。

status：

- `active`
- `revoked`

### 3.3 Policy

```json
{
  "approval_threshold": 2,
  "witness_threshold": 1,
  "required_approval_scopes": ["execute"],
  "distinct_approver_principals": true
}
```

Registry 建立時必須至少存在：

- 一個 active Provider principal；
- `approval_threshold` 個 active Approver principals；
- 一個 active Checkpoint principal；
- `witness_threshold` 個 active Witness principals；
- 一個 active Release principal。

## 4. Threshold Approval Set

格式：`ULCS-Threshold-Approval-Set`  
版本：`1.0`

v1.0 不改寫 v0.9 `ULCS-Signed-Approval`。Approval Set 直接聚合多份既有 Ed25519 Approval。

### 4.1 綁定內容

Approval Set 固定：

- Review Bundle digest；
- Input Bundle digest；
- Provider Attestation digest；
- threshold；
- distinct-principal policy；
- 完整 Signed Approval 陣列；
- created timestamp；
- canonical Approval Set digest。

### 4.2 驗證條件

每份 Approval 必須：

1. 綁定同一 Review Bundle。
2. 綁定同一 Input Bundle。
3. 綁定同一 Provider Attestation。
4. decision 為 `approve`。
5. 包含 Registry 要求的所有 scopes。
6. 使用不同 key ID。
7. key 在 Registry 中為 active `approver`。
8. Approval principal 與 Registry principal 相同。
9. Transparency Log 包含對應 `approval-issued` 事件。
10. key 未在該 checkpointed Log 中出現 `key-revoked` 事件。

Approval 數量必須至少達到 `approval_threshold`。

`distinct_approver_principals=true` 時，不同 key 但相同 principal 仍只計為一個核准主體。

## 5. Checkpoint Witness

格式：`ULCS-Checkpoint-Witness`  
版本：`1.0`

Witness Statement 固定：

- Transparency Checkpoint digest；
- log head；
- entry count；
- Witness principal；
- Witness key ID；
- issued timestamp；
- Ed25519 signature。

Witness 是對 checkpoint 的外部觀測證明，不會附加回已 checkpoint 的 Transparency Log。

這個設計避免以下循環：

```text
checkpoint(log head A)
→ append witness event
→ log head B
→ old checkpoint no longer describes current log
```

## 6. Witness Set

格式：`ULCS-Witness-Set`  
版本：`1.0`

Witness Set 聚合多份 Witness Statement，並固定：

- checkpoint digest；
- threshold；
- Witness Statement 陣列；
- created timestamp；
- canonical digest。

每份 Witness 必須：

- 綁定同一 checkpoint；
- 使用不同 key ID；
- 使用不同 Registry principal；
- key 在 Registry 中為 active `witness`；
- signature 驗證成功；
- key 未在 checkpointed Log 中撤銷。

Witness principal 數量必須至少達到 `witness_threshold`。

## 7. Governed Release Bundle

格式：`ULCS-Governed-Release-Bundle`  
版本：`1.0`  
演算法：`ed25519`

### 7.1 目錄結構

```text
release/
├── release-bundle.json
├── review/
│   ├── review-bundle.json
│   ├── provider-proposal.json
│   ├── intent-plan.json
│   ├── workflow.sos
│   ├── artifact-contract.json
│   ├── capability-policy.json
│   └── intent-bundle.json
├── input/
│   ├── input-contract.json
│   ├── input-bundle.json
│   └── inputs/...
└── governance/
    ├── trust-registry.json
    ├── provider-attestation.json
    ├── approval-set.json
    ├── transparency-log.json
    ├── transparency-checkpoint.json
    └── witness-set.json
```

Release Bundle 不包含 private key。

### 7.2 Release Manifest 綁定內容

`release-bundle.json` 固定：

- Registry digest；
- Review Bundle digest；
- Input Bundle digest；
- Provider Attestation digest；
- Approval Set digest；
- Transparency Log digest；
- Checkpoint digest；
- Witness Set digest；
- bundle 內每個非 manifest 檔案的相對路徑、SHA-256 與 size；
- Release signer principal 與 key ID；
- created timestamp；
- Ed25519 signature；
- 包含 signature 的完整 Release digest。

Release signer key 必須是 Registry 中 active `release` key。

### 7.3 File-set closure

驗證時，實際檔案集合必須與 manifest 完全相同：

- 缺少檔案：拒絕；
- 新增未列入檔案：拒絕；
- symlink：拒絕；
- size 不一致：拒絕；
- SHA-256 不一致：拒絕；
- path escape：拒絕。

## 8. 驗證順序

`ulcs-release verify` 與 `execute` 使用以下順序：

1. 驗證 Release Manifest canonical digest。
2. 驗證 bundle file-set closure、size 與 SHA-256。
3. 以外部 Root public key 驗證 Trust Registry。
4. 由 Registry 解析 Release key 並驗證 Release signature。
5. 重新讀取 Review Bundle 與 Input Bundle。
6. 驗證 Release Manifest 的所有 component binding digest。
7. 由 Registry 解析 Provider key，驗證 Provider Attestation。
8. 驗證 Provider 事件與 revocation 狀態。
9. 驗證 Threshold Approval Set。
10. 由 Registry 解析 Checkpoint key，驗證 Transparency Log 與 Checkpoint。
11. 驗證 Checkpoint key revocation 狀態。
12. 驗證 Witness Set。
13. 完整鏈通過後，才允許執行。

## 9. 執行分層

`ulcs-release execute` 完成全部 v1.0 驗證後，會從 Registry 解析 Provider、其中一個已驗證 Approver、Checkpoint public key，並委派給既有 v0.9 Trusted Runner。

只選取一份 Approval 傳給 v0.9 Runner，不代表 threshold 被降為 1。Threshold 已在委派之前由 v1.0 Approval Set 驗證；v0.9 Runner 只負責重用其成熟的快照建立與 runtime override 阻擋邏輯。

執行環境會增加：

- `ULCS_RELEASE_BUNDLE_DIGEST`
- `ULCS_TRUST_REGISTRY_DIGEST`
- `ULCS_APPROVAL_SET_DIGEST`
- `ULCS_WITNESS_SET_DIGEST`

並保留 v0.9 Trusted Runner 提供的 Review、Input、Attestation、Approval 與 Transparency digest 環境變數。

## 10. CLI

### 10.1 Registry

```bash
ulcs-govern registry \
  --registry-id production-2026-07 \
  --root-private-key root-private.pem \
  --provider-key provider-a=provider-public.pem \
  --approver-key reviewer-a=reviewer-a-public.pem \
  --approver-key reviewer-b=reviewer-b-public.pem \
  --checkpoint-key checkpoint-a=checkpoint-public.pem \
  --witness-key witness-a=witness-public.pem \
  --release-key release-a=release-public.pem \
  --approval-threshold 2 \
  --witness-threshold 1 \
  --output trust-registry.json
```

### 10.2 Approval Set

```bash
ulcs-govern approvals \
  review/review-bundle.json \
  input/input-bundle.json \
  provider-attestation.json \
  --approval approval-a.json \
  --approval approval-b.json \
  --registry trust-registry.json \
  --root-public-key root-public.pem \
  --transparency-log transparency-log.json \
  --output approval-set.json
```

### 10.3 Witness

```bash
ulcs-govern witness transparency-checkpoint.json \
  --private-key witness-private.pem \
  --witness witness-a \
  --output witness-a.json

ulcs-govern witnesses transparency-checkpoint.json \
  --witness witness-a.json \
  --registry trust-registry.json \
  --root-public-key root-public.pem \
  --transparency-log transparency-log.json \
  --output witness-set.json
```

### 10.4 Release

```bash
ulcs-release build \
  review/review-bundle.json \
  input/input-bundle.json \
  provider-attestation.json \
  approval-set.json \
  transparency-log.json \
  transparency-checkpoint.json \
  witness-set.json \
  trust-registry.json \
  --root-public-key root-public.pem \
  --private-key release-private.pem \
  --signer release-a \
  --output-dir release

ulcs-release verify release \
  --root-public-key root-public.pem

ulcs-release execute release \
  --root-public-key root-public.pem \
  -- --yes --json
```

第一個獨立 `--` 是 Release CLI 與底層 Runtime 參數的唯一分界。

## 11. 相容性

v1.0 沒有修改：

- `.sos` 表面語法；
- Runtime Adapter API；
- LOG v0.6；
- Artifact、Manifest 與 Checkpoint v0.6；
- Intent Request／Bundle v0.7；
- Provider Proposal／Review Bundle v0.8；
- Provider Attestation、Signed Approval、Transparency Log 與 Checkpoint v0.9。

v0.8 HMAC 與 v0.9 單一 Ed25519 Trusted Runner 仍可使用。v1.0 是新的、更高層治理入口，不會讓舊格式冒充 v1.0 Release Bundle。

## 12. 安全邊界

### 12.1 Registry snapshot rollback

Trust Registry 是 Root-signed snapshot，不是自動更新的全球 PKI。

一份舊 Registry 只要 Root signature 仍正確，純密碼學驗證仍會成功。部署者必須額外保存：

- 最低允許 Registry version 或 digest；
- Registry 發布序號；
- 外部 witness／append-only log；
- anti-rollback storage；
- Root-signed replacement policy。

v1.0 reference implementation沒有宣稱自動解決 Registry rollback。

### 12.2 Revocation 時間邊界

Transparency Log revocation 只能描述 checkpoint 建立前已進入該 Log 的事件。

checkpoint 之後才發生的 key compromise，需要新的 Log head、Checkpoint、Witness Set 與 Release Bundle。舊 release 不會自動得知未來事件。

### 12.3 Principal identity

Registry principal 是治理命名空間，不自動證明現實世界身分、公司職權、法律授權或生物身分。

### 12.4 Threshold 不是拜占庭共識

M-of-N Approval 與 Witness threshold 是本地可驗證政策，不是分散式共識演算法，也不處理網路分割、活性或惡意多數。

### 12.5 Key custody

Reference keygen 產生未加密 PKCS8 PEM。正式部署應使用 KMS、HSM、TPM、hardware token、offline signing ceremony 或 secret manager。

### 12.6 Execution isolation

完整治理鏈不等於 OS sandbox。正式部署仍需：

- 低權限帳戶；
- container／VM；
- filesystem namespace；
- network egress policy；
- CPU／RAM／process limits；
- secret isolation；
- 禁止 Agent 直接繞過 `ulcs-release` 呼叫底層 runtime。

## 13. v1.0 的完成判準

Reference implementation 的 v1.0 完成判準是：

```text
Root key
→ signed Registry
→ Provider Attestation
→ two independent Signed Approvals
→ 2-of-2 Approval Set
→ checkpointed Transparency Log
→ independent Witness
→ Witness Set
→ signed closed-file Release Bundle
→ Root-only verification
→ trusted snapshot execution
```

上述鏈必須在 Ubuntu／Windows 與 Python 3.11、3.12、3.13 全部通過。
