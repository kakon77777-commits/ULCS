# ULCS — Universal Language Composition Substrate

ULCS（全語言組合基底）不是把所有程式語言改寫成單一語法，而是讓不同語言保留自身語義，並在同一個可型別化、可驗證、可治理、可重現、可恢復、可核准、可簽署與可發布的 Language Operator Graph 中組合。

目前版本：**v1.0.0**

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
v1.0  Root Trust Registry、Threshold Approval、Witness、Governed Release
```

## ULCS 的核心

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
- named input alias
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

## 從意圖到受治理發布

```text
Human / AI Intent
→ deterministic Intent Compiler
→ Provider Proposal
→ Review Bundle
→ exact Input Bundle
→ Provider Attestation
→ multiple Signed Approvals
→ Threshold Approval Set
→ Transparency Log
→ signed Checkpoint
→ independent Witness Set
→ Root-signed Trust Registry
→ signed Governed Release Bundle
→ Root-only verification
→ trusted snapshot execution
```

## v1.0 信任模型

v0.9 由命令列分別提供 Provider、Approver 與 Checkpoint public key。

v1.0 改為：

```text
External Root Public Key
        │
        ▼
Root-signed Trust Registry
        │
        ├── Provider key
        ├── Approver keys
        ├── Checkpoint key
        ├── Witness keys
        └── Release key
```

執行端只需要預先信任 Root public key。Operational public keys、principal、role 與 status 由 Root-signed Registry 固定。

Registry roles：

- `provider`
- `approver`
- `checkpoint`
- `witness`
- `release`

## Threshold Approval

v1.0 重用 v0.9 `ULCS-Signed-Approval`，並由 `ULCS-Threshold-Approval-Set` 聚合成 M-of-N policy。

例如：

```json
{
  "approval_threshold": 2,
  "witness_threshold": 1,
  "required_approval_scopes": ["execute"],
  "distinct_approver_principals": true
}
```

兩把 key 若屬於同一 principal，在 `distinct_approver_principals=true` 時仍只算一個核准主體。

每份 Approval 必須：

- 綁定同一 Review Bundle；
- 綁定同一 Input Bundle；
- 綁定同一 Provider Attestation；
- decision 為 `approve`；
- 含必要 scope；
- key 在 Registry 中為 active `approver`；
- principal 與 Registry 一致；
- Transparency Log 包含相應 `approval-issued` 事件；
- key 未在 checkpointed Log 中撤銷。

## External Witness

Witness 對已完成的 Transparency Checkpoint 簽署，不寫回該 Log：

```text
Transparency Log
→ Checkpoint
→ Witness Statement A
→ Witness Statement B
→ Witness Set
```

這避免「Witness event 改變 log head，使原 checkpoint 立即過時」的循環。

Witness threshold 與 Witness principals 同樣由 Trust Registry 固定。

## Governed Release Bundle

v1.0 把整條治理鏈封裝為自包含發布單位：

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

`release-bundle.json` 固定：

- Registry／Review／Input／Attestation／Approval Set digest
- Transparency Log／Checkpoint／Witness Set digest
- 每個 bundle 檔案的相對路徑、SHA-256 與 size
- Release signer principal 與 key ID
- Ed25519 release signature
- 完整 release digest

驗證時，缺檔、新增未列入檔案、symlink、size 或 SHA-256 改變都會被拒絕。

## 安裝與測試

```bash
python -m pip install -e .
python -m unittest discover -s tests -v
```

v1.0 需要 Python 3.11 以上，並使用 `cryptography` 的 Ed25519 primitive。

主要命令：

```bash
ulcs --help
ulcs-intent --help
ulcs-provider --help
ulcs-approve --help
ulcs-approved --help
ulcs-input --help
ulcs-sign --help
ulcs-log --help
ulcs-trusted --help
ulcs-govern --help
ulcs-release --help
```

## `.sos` 語法

```text
<role> <node-id> = <language>{
    <原生語言內容>
} [from <node>[.<field>] [as <name>], ...]
```

角色目前是描述性標籤：`source`、`extract`、`transform`、`store`、`run`。

單一輸入直接傳給 Runtime；多輸入形成具名映射，預設鍵為來源節點名稱，也可以用 `as` 指定。

## v1.0 端到端流程

### 1. 建立 Review 與 exact Input Bundle

```bash
ulcs-provider examples/provider_proposal_v0.9.json \
  --output-dir output/v1.0/review --json

ulcs-input capture examples/input_contract_v0.9.json \
  --output-dir output/v1.0/input --json
```

### 2. 產生 Root 與 operational keypairs

```bash
ulcs-sign keygen \
  --private-key keys/root-private.pem \
  --public-key keys/root-public.pem

ulcs-sign keygen \
  --private-key keys/provider-private.pem \
  --public-key keys/provider-public.pem
```

以相同方式建立至少兩組 Approver、一組 Checkpoint、一組 Witness 與一組 Release keypair。

### 3. 建立 Provider Attestation 與多份 Approval

```bash
ulcs-sign provider \
  output/v1.0/review/provider-proposal.json \
  --private-key keys/provider-private.pem \
  --output provider-attestation.json \
  --log transparency-log.json

ulcs-sign approve \
  output/v1.0/review/review-bundle.json \
  output/v1.0/input/input-bundle.json \
  provider-attestation.json \
  --provider-public-key keys/provider-public.pem \
  --private-key keys/reviewer-a-private.pem \
  --approver reviewer-a \
  --output approval-a.json \
  --log transparency-log.json
```

Reviewer B 以自己的 key 產生第二份 Approval。

### 4. 建立 Root-signed Registry

```bash
ulcs-govern registry \
  --registry-id production-2026-07 \
  --root-private-key keys/root-private.pem \
  --provider-key provider-a=keys/provider-public.pem \
  --approver-key reviewer-a=keys/reviewer-a-public.pem \
  --approver-key reviewer-b=keys/reviewer-b-public.pem \
  --checkpoint-key checkpoint-a=keys/checkpoint-public.pem \
  --witness-key witness-a=keys/witness-public.pem \
  --release-key release-a=keys/release-public.pem \
  --approval-threshold 2 \
  --witness-threshold 1 \
  --output trust-registry.json
```

### 5. 聚合 Threshold Approval Set

```bash
ulcs-govern approvals \
  output/v1.0/review/review-bundle.json \
  output/v1.0/input/input-bundle.json \
  provider-attestation.json \
  --approval approval-a.json \
  --approval approval-b.json \
  --registry trust-registry.json \
  --root-public-key keys/root-public.pem \
  --transparency-log transparency-log.json \
  --output approval-set.json
```

### 6. 建立 Checkpoint 與 Witness Set

```bash
ulcs-log checkpoint transparency-log.json \
  --signer checkpoint-a \
  --private-key keys/checkpoint-private.pem \
  --output transparency-checkpoint.json

ulcs-govern witness transparency-checkpoint.json \
  --private-key keys/witness-private.pem \
  --witness witness-a \
  --output witness-a.json

ulcs-govern witnesses transparency-checkpoint.json \
  --witness witness-a.json \
  --registry trust-registry.json \
  --root-public-key keys/root-public.pem \
  --transparency-log transparency-log.json \
  --output witness-set.json
```

### 7. 建立、驗證與執行 Release Bundle

```bash
ulcs-release build \
  output/v1.0/review/review-bundle.json \
  output/v1.0/input/input-bundle.json \
  provider-attestation.json \
  approval-set.json \
  transparency-log.json \
  transparency-checkpoint.json \
  witness-set.json \
  trust-registry.json \
  --root-public-key keys/root-public.pem \
  --private-key keys/release-private.pem \
  --signer release-a \
  --output-dir release

ulcs-release verify release \
  --root-public-key keys/root-public.pem

ulcs-release execute release \
  --root-public-key keys/root-public.pem \
  -- --yes --json
```

第一個獨立 `--` 是 Release CLI 與 Runtime 參數的唯一分界。

## v0.9 與舊版相容路徑

v0.9 單一 Ed25519 Trusted Runner 仍可使用：

```bash
ulcs-trusted \
  review/review-bundle.json \
  input/input-bundle.json \
  provider-attestation.json \
  signed-approval.json \
  --provider-public-key provider-public.pem \
  --approver-public-key approver-public.pem \
  --transparency-log transparency-log.json \
  --log-checkpoint transparency-checkpoint.json \
  --checkpoint-public-key checkpoint-public.pem \
  -- --yes --json
```

v0.8 HMAC Approval Gate、v0.7 Intent Compiler 與 v0.1–v0.6 Runtime／Artifact 路徑均保留。

## 安全邊界

- Root-signed Registry 是 signed snapshot，不是自動更新的全球 PKI。
- 舊 Registry 仍需部署層的 version floor、anti-rollback storage 或外部發布紀錄才能防止回滾。
- Registry principal 是治理命名空間，不自動證明法律或現實身分。
- M-of-N threshold 是本地 policy，不是拜占庭共識演算法。
- Checkpoint 後發生的 key compromise，需要新的 Log、Checkpoint、Witness Set 與 Release Bundle。
- Reference keygen 產生未加密 PKCS8 PEM；正式部署應使用 KMS、HSM、TPM 或 hardware token。
- Signed governance 不等於 OS sandbox；仍需低權限帳戶、container／VM、filesystem isolation、network egress policy、CPU／RAM limits 與 secret isolation。
- 若 Agent 仍可直接呼叫底層 `ulcs` 或 Python module，治理入口可以被繞過；部署者必須以 OS 權限固定唯一執行入口。

完整規格：[`docs/TRUST_REGISTRY_RELEASE_SPEC_v1.0.md`](docs/TRUST_REGISTRY_RELEASE_SPEC_v1.0.md)

v0.9 規格：[`docs/INPUT_ATTESTATION_TRANSPARENCY_SPEC_v0.9.md`](docs/INPUT_ATTESTATION_TRANSPARENCY_SPEC_v0.9.md)

v0.8 規格：[`docs/PROVIDER_APPROVAL_SPEC_v0.8.md`](docs/PROVIDER_APPROVAL_SPEC_v0.8.md)

v0.7 規格：[`docs/INTENT_COMPILER_SPEC_v0.7.md`](docs/INTENT_COMPILER_SPEC_v0.7.md)
