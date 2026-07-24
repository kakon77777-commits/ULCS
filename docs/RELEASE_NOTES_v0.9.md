# ULCS v0.9 Release Notes

版本：0.9.0  
日期：2026-07-24

## Added

- `ULCS-Input-Contract` v0.9
- `ULCS-Input-Bundle` v0.9
- local file、inline UTF-8 text 與 inline canonical JSON capture
- Input source／mount path confinement、symlink rejection、單檔與總 bytes 配額
- 每個 mounted input 的 SHA-256 與 size
- Ed25519 keypair generation 與 `sha256:<public-key-fingerprint>` key ID
- `ULCS-Provider-Attestation` v0.9
- `ULCS-Signed-Approval` v0.9
- Review Bundle、Input Bundle 與 Provider Attestation 三重 digest binding
- hash-chained `ULCS-Transparency-Log` v0.9
- `provider-attested`、`approval-issued`、`approval-rejected`、`key-revoked` events
- Ed25519-signed `ULCS-Transparency-Checkpoint` v0.9
- `ulcs-input`、`ulcs-sign`、`ulcs-log`、`ulcs-trusted`
- Trusted Runner verified snapshot 與 `inputs/` mount
- Provider／Approver／Checkpoint signer key revocation enforcement
- Windows／Ubuntu、Python 3.11–3.13 端到端驗證流程

## Trust separation

v0.9 將三個角色分開：

1. Provider 使用自己的 Ed25519 key 證明其提出了特定 Proposal。
2. Approver 使用另一組 Ed25519 key 核准 exact Review、Input 與 Provider Attestation。
3. Transparency operator 使用第三組 Ed25519 key 固定 Log head 與 entry count。

任何一個角色都不因持有自己的 private key 而自動取得其他角色的權限。

## Compatibility

- `.sos` 語法不變。
- Runtime Adapter API 不變。
- LOG、Artifact、Manifest 與 Execution Checkpoint 維持 v0.6。
- Intent Request／Intent Bundle 維持 v0.7。
- Provider Proposal／Review Bundle／HMAC Approval Record 維持 v0.8。
- `ulcs-approved` 繼續處理 v0.8 HMAC 核准。
- `ulcs-trusted` 處理 v0.9 Input、Ed25519 與 Transparency 鏈。

## Migration

既有 v0.8 使用者不必轉換現有 Approval Record。

需要 signer separation 或 exact input binding 的新工作流應改用：

```text
ulcs-provider
ulcs-input
ulcs-sign
ulcs-log
ulcs-trusted
```

v0.8 HMAC Approval Record 不能轉換為 Ed25519 簽章。若要建立 v0.9 授權，必須由對應 private key 對 v0.9 canonical payload 重新簽署。

## Safety boundaries

- Ed25519 證明 private-key possession，不自動證明現實身分、組織職權或法律授權。
- Reference keygen 產生未加密 PKCS8 PEM；正式部署應使用 KMS、HSM、TPM、hardware token 或 secret manager。
- 本地 Transparency Log 是 tamper-evident hash chain，不是不可變帳本或共識網路。
- rollback detection 需要把 signed checkpoint 保存至攻擊者不能同步改寫的位置。
- v0.9 尚未固定 live HTTP、database query、queue、stdin、secret manager 或 device input。
- Transparency Log 目前是 single-writer reference implementation。
- Signed governance 不取代低權限帳戶、container／VM、filesystem／network isolation 與 process limits。
