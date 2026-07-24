# ULCS v1.0 Release Notes

日期：2026-07-24  
代號：Root-Anchored Governed Release

## 摘要

ULCS v1.0 將 v0.9 的單次 Ed25519 信任鏈提升為可發布、可搬移、可由單一 Root trust anchor 驗證的治理系統。

核心鏈：

```text
Root Public Key
→ Root-signed Trust Registry
→ Provider Attestation
→ M-of-N Signed Approvals
→ Threshold Approval Set
→ Transparency Checkpoint
→ independent Witness Set
→ Release signature
→ closed-file Governed Release Bundle
→ reverified execution snapshot
```

## 新增格式

- `ULCS-Trust-Registry` v1.0
- `ULCS-Threshold-Approval-Set` v1.0
- `ULCS-Checkpoint-Witness` v1.0
- `ULCS-Witness-Set` v1.0
- `ULCS-Governed-Release-Bundle` v1.0

## 新增 CLI

- `ulcs-govern`
  - `registry`
  - `verify-registry`
  - `approvals`
  - `witness`
  - `witnesses`
- `ulcs-release`
  - `build`
  - `verify`
  - `execute`

## Trust Registry

Trust Registry 由外部 Root private key 簽署，內嵌 operational Ed25519 public keys。

角色：

- Provider
- Approver
- Checkpoint signer
- Witness
- Release signer

每個 key 固定 principal、key ID、roles、status 與 public-key PEM。

執行端只需持有 Root public key；不再由每次命令列任意選擇 Provider、Approver 與 Checkpoint public key。

## Threshold Approval

v1.0 保留 v0.9 Signed Approval 格式，新增 M-of-N 聚合層。

Reference CI 使用：

```text
approval threshold = 2
Reviewer A key != Reviewer B key
Reviewer A principal != Reviewer B principal
required scope = execute
```

Approval 必須存在於 checkpointed Transparency Log，且 key 同時通過 Registry role、principal、status 與 Log revocation 檢查。

## Witness

Witness Statement 獨立簽署 Transparency Checkpoint，不附加回該 Log。

Witness Set 執行：

- checkpoint digest binding
- log-head binding
- entry-count binding
- distinct key enforcement
- distinct principal enforcement
- Registry witness-role enforcement
- threshold enforcement

## Governed Release Bundle

Release Bundle 封裝：

- 完整 Review Bundle
- 完整 Input Bundle 與 exact bytes
- Trust Registry
- Provider Attestation
- Threshold Approval Set
- Transparency Log
- Transparency Checkpoint
- Witness Set

Release Manifest 固定所有 component digests，以及每個檔案的相對路徑、SHA-256 與 size。

任何缺檔、額外檔案、symlink、size 改變或 digest 改變都會被拒絕。

## Execution snapshot

`ulcs-release execute` 不直接長時間信任來源目錄。

流程：

1. 驗證來源 Release Bundle。
2. 只複製 manifest 列出的檔案。
3. 拒絕 symlink chain。
4. 以已解析的 in-memory manifest 建立 snapshot manifest。
5. 重新驗證整個 snapshot。
6. 比較來源與 snapshot release digest。
7. 通過後才委派給 v0.9 Trusted Runner。

## 相容性

未修改：

- `.sos` 語法
- Runtime Adapter API
- LOG v0.6
- Artifact／Manifest／Checkpoint v0.6
- Intent v0.7
- Provider／Review v0.8
- Input／Attestation／Approval／Transparency v0.9

舊入口仍保留：

- `ulcs`
- `ulcs-intent`
- `ulcs-provider`
- `ulcs-approved`
- `ulcs-trusted`

## 已知邊界

- Registry 是 signed snapshot，不自動防止舊 Registry rollback。
- checkpoint 後的 key compromise 不會回溯改變已發布 Release Bundle。
- principal 是治理命名，不是法律或生物身分證明。
- threshold policy 不是拜占庭共識。
- Reference PEM private keys 未加密。
- Governance signature 不等於 OS sandbox。
- 正式部署仍須限制底層 runtime，使 Agent 無法繞過 `ulcs-release`。

## 驗證矩陣

正式候選必須通過：

```text
Ubuntu  × Python 3.11
Ubuntu  × Python 3.12
Ubuntu  × Python 3.13
Windows × Python 3.11
Windows × Python 3.12
Windows × Python 3.13
```

每個矩陣會實際建立七組 Ed25519 identity、Root Registry、兩份核准、Checkpoint、Witness、Release Bundle，並從該 Release Bundle 執行 ERROR／FATAL 日誌分析。
