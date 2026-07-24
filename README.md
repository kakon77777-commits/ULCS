# ULCS — Universal Language Composition Substrate

ULCS（全語言組合基底）不是把所有程式語言改寫成單一語法，而是讓不同語言保留自身語義，並在同一個可型別化、可驗證、可治理、可重現、可恢復、可由意圖編譯、核准與簽署的 Language Operator Graph 中組合。

目前版本：**v0.9.0**

## 版本主軸

```text
v0.1  跨語言線性管線
v0.2  Language Operator Graph
v0.3  能力政策與 Runtime 外掛
v0.4  資源範圍、平行 DAG、配額、污染追蹤
v0.5  內容定址快取、Manifest、重放驗證
v0.6  Artifact Contract、checkpoint、resume
v0.7  review-first Intent Compiler
v0.8  Provider Contract、Review Bundle、HMAC Approval Gate
v0.9  Input Contract、Ed25519 Attestation、Transparency Checkpoint
```

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

### 治理、可重現性與恢復

- audit／enforce 能力政策
- `capability@resource` 資源範圍
- 節點數、工作者與輸出 bytes 配額
- 資料污染來源與 DAG 傳播
- Execution Trace
- Canonical JSON 與 SHA-256
- 逐節點 fingerprint 與 input／output digest
- 內容定址快取
- Execution Manifest 與重放驗證
- Artifact Contract 與受限 schema 驗證
- 內容定址 Artifact Store
- 每層原子 checkpoint
- 部分或完整 resume

## Intent、Review 與核准

### v0.7 Intent Compiler

- `ULCS-Intent-Request`
- `ready`／`needs_clarification`／`rejected`
- deterministic profiles
- 生成 `.sos`、Artifact Contract、enforce policy 與 Intent Plan
- 生成候選重新經 parser、DAG、contract 與 policy validator
- 不自動執行生成結果

### v0.8 Provider Contract

外部 AI／Agent 只能提出意圖資料，不能自行提交：

```text
workflow
policy
claims
status
ready
approval
signature
```

Review Bundle 固定：

```text
provider-proposal.json
intent-plan.json
workflow.sos
artifact-contract.json
capability-policy.json
intent-bundle.json
```

v0.8 以 HMAC-SHA256 Approval Record 控制 `ulcs-approved`。

### v0.9 Trusted Governance

v0.9 在 v0.8 Review Bundle 外再固定：

- exact input bytes
- Provider public-key identity
- Reviewer public-key identity
- transparency event order
- signed log head
- key revocation events

完整鏈：

```text
Provider Proposal
→ Review Bundle

Input Contract
→ Input Bundle

Provider Proposal + Provider private key
→ Provider Attestation

Review + Input + Attestation + Reviewer private key
→ Signed Approval

Attestation + Approval events
→ Transparency Log
→ signed Transparency Checkpoint

ulcs-trusted
→ verified temporary snapshot
→ existing ULCS Runtime
```

## 安裝與測試

```bash
python -m pip install -e .
python -m unittest discover -s tests -v
```

v0.9 使用 `cryptography` 的 Ed25519 primitive，會由套件相依性安裝。

主要命令：

```bash
ulcs --list-languages
ulcs --list-capabilities
ulcs-intent --list-profiles
ulcs-provider --help
ulcs-approve --help
ulcs-approved --help
ulcs-input --help
ulcs-sign --help
ulcs-log --help
ulcs-trusted --help
```

## `.sos` 語法

```text
<role> <node-id> = <language>{
    <原生語言內容>
} [from <node>[.<field>] [as <name>], ...]
```

角色目前是描述性標籤：`source`、`extract`、`transform`、`store`、`run`。

單一輸入直接傳給 Runtime；多輸入形成具名映射，預設鍵是來源節點名稱，也可以用 `as` 指定。

## v0.9 端到端範例

### 1. 編譯 Provider Proposal

範例 workflow 會讀取 Trusted Runner 快照中的 `./inputs/*.log`：

```bash
ulcs-provider examples/provider_proposal_v0.9.json \
  --output-dir output/v0.9/review \
  --json
```

### 2. 捕獲 exact inputs

```bash
ulcs-input capture examples/input_contract_v0.9.json \
  --output-dir output/v0.9/input \
  --json
```

Input Contract 支援：

- `file`
- `inline-text`
- `inline-json`

所有 mount 必須位於 `inputs/`，並固定 SHA-256 與 byte size。

### 3. 產生三組 Ed25519 identity

Provider、Approver 與 Transparency Checkpoint signer 使用不同 keypair：

```bash
ulcs-sign keygen \
  --private-key output/v0.9/keys/provider-private.pem \
  --public-key output/v0.9/keys/provider-public.pem

ulcs-sign keygen \
  --private-key output/v0.9/keys/approver-private.pem \
  --public-key output/v0.9/keys/approver-public.pem

ulcs-sign keygen \
  --private-key output/v0.9/keys/checkpoint-private.pem \
  --public-key output/v0.9/keys/checkpoint-public.pem
```

### 4. Provider Attestation

```bash
ulcs-sign provider \
  output/v0.9/review/provider-proposal.json \
  --private-key output/v0.9/keys/provider-private.pem \
  --output output/v0.9/provider-attestation.json \
  --log output/v0.9/transparency-log.json
```

Provider Attestation 綁定：

- proposal digest
- Provider ID
- model description
- public-key fingerprint
- timestamp

它不授予執行權限。

### 5. Signed Approval

```bash
ulcs-sign approve \
  output/v0.9/review/review-bundle.json \
  output/v0.9/input/input-bundle.json \
  output/v0.9/provider-attestation.json \
  --provider-public-key output/v0.9/keys/provider-public.pem \
  --private-key output/v0.9/keys/approver-private.pem \
  --approver reviewer-v09 \
  --reason "Reviewed exact workflow, policy, contract and input bytes." \
  --output output/v0.9/signed-approval.json \
  --log output/v0.9/transparency-log.json
```

Signed Approval 同時固定：

- Review Bundle digest
- Input Bundle digest
- Provider Attestation digest
- decision
- scopes
- approver key ID

### 6. Signed Transparency Checkpoint

```bash
ulcs-log checkpoint output/v0.9/transparency-log.json \
  --signer log-operator \
  --private-key output/v0.9/keys/checkpoint-private.pem \
  --output output/v0.9/transparency-checkpoint.json

ulcs-log verify-checkpoint \
  output/v0.9/transparency-log.json \
  output/v0.9/transparency-checkpoint.json \
  --public-key output/v0.9/keys/checkpoint-public.pem
```

### 7. Trusted execution

```bash
ulcs-trusted \
  output/v0.9/review/review-bundle.json \
  output/v0.9/input/input-bundle.json \
  output/v0.9/provider-attestation.json \
  output/v0.9/signed-approval.json \
  --provider-public-key output/v0.9/keys/provider-public.pem \
  --approver-public-key output/v0.9/keys/approver-public.pem \
  --transparency-log output/v0.9/transparency-log.json \
  --log-checkpoint output/v0.9/transparency-checkpoint.json \
  --checkpoint-public-key output/v0.9/keys/checkpoint-public.pem \
  -- --artifact-mode all \
  --artifact-dir output/v0.9/artifacts \
  --yes --json \
  --emit-trace output/v0.9/trace.json
```

Trusted Runner 會：

1. 驗證 Review Bundle。
2. 驗證 Input Bundle 與 exact bytes。
3. 驗證 Provider Attestation。
4. 驗證 Signed Approval。
5. 驗證 Transparency Log hash chain。
6. 驗證 signed checkpoint。
7. 拒絕已撤銷的 Provider、Approver 或 Checkpoint key。
8. 將 Review 與 Input 掛載到暫存快照。
9. 將 Runtime `cwd` 固定為該快照。
10. 執行既有 ULCS Runtime。

## Key revocation

```bash
ulcs-log revoke-key output/v0.9/transparency-log.json \
  sha256:<public-key-fingerprint> \
  --reason "Key compromised"
```

撤銷只有在新的可信 checkpoint 包含該事件後，才能被依賴該 checkpoint 的執行者看到。

## v0.8 相容路徑

HMAC Approval Gate 仍可使用：

```bash
export ULCS_APPROVAL_KEY='replace-with-a-random-secret'

ulcs-approve approve output/v0.8/review-bundle.json \
  --approver reviewer@example \
  --key-env ULCS_APPROVAL_KEY \
  --output output/v0.8/approval.json

ulcs-approved \
  output/v0.8/review-bundle.json \
  output/v0.8/approval.json \
  --key-env ULCS_APPROVAL_KEY \
  -- --yes --json
```

v0.8 HMAC 與 v0.9 Ed25519 是不同格式與不同信任模型，不會互相冒充。

## 安全邊界

- Ed25519 證明簽署者持有 private key，不自動證明現實身分或法律授權。
- Reference keygen 產生未加密 PKCS8 PEM；正式部署應使用 KMS、HSM、TPM、hardware token 或 secret manager。
- 本地 Transparency Log 是 tamper-evident hash chain，不是區塊鏈或不可變儲存。
- 回滾偵測需要把 signed checkpoint 保存到攻擊者不能同步改寫的位置。
- v0.9 Input Contract 固定 local file、inline text 與 inline JSON；尚未固定 live HTTP、資料庫、queue、stdin、secret manager 或裝置輸入。
- Transparency Log 目前是單一 writer 模型，沒有分散式共識或跨程序 writer lock。
- Signed governance 不等於 OS sandbox；正式部署仍需低權限帳戶、container／VM、filesystem isolation、network egress policy 與 secret isolation。

完整規格：[`docs/INPUT_ATTESTATION_TRANSPARENCY_SPEC_v0.9.md`](docs/INPUT_ATTESTATION_TRANSPARENCY_SPEC_v0.9.md)

v0.8 規格：[`docs/PROVIDER_APPROVAL_SPEC_v0.8.md`](docs/PROVIDER_APPROVAL_SPEC_v0.8.md)

v0.7 規格：[`docs/INTENT_COMPILER_SPEC_v0.7.md`](docs/INTENT_COMPILER_SPEC_v0.7.md)
