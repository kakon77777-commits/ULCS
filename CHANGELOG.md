# Changelog

## 0.8.0 — 2026-07-23

### Added

- `ULCS-Intent-Provider-Proposal` v0.8 與嚴格欄位白名單
- `ULCS-Review-Bundle` v0.8、固定 reviewed file 集合、逐檔 SHA-256／size 與 canonical bundle digest
- `ULCS-Approval-Record` v0.8
- `approve`／`reject` 決策與 `execute` scope
- HMAC-SHA256 完整性核准，key 僅從環境變數或 key file 載入
- `ulcs-provider`、`ulcs-approve`、`ulcs-approved` CLI
- Approved Runner 驗證後暫存快照執行
- Provider Proposal、Review Bundle、Approval Gate 與安全邊界規格
- Provider Proposal 範例
- 跨平台 Provider → Review → Approval → Approved Runner 端到端 CI

### Governance

- Provider 不得提交 `workflow`、`policy`、`claims`、`status`、`ready`、`approval` 或 `signature`
- 只有 deterministic Intent Compiler 與既有 validator 可產生 `ready`
- Review Bundle 拒絕缺檔、檔名集合改變、符號連結、size／digest 改變與非 ready metadata
- Approval 必須綁定同一 Review Bundle digest、HMAC、`approve` 決策與 `execute` scope
- Approved Runner 禁止覆寫 policy、contract、capability 規則、plugin、cwd、db、resource limits、cache 與 resume
- 核准檔案在執行前複製到暫存快照並重新驗證 size 與 SHA-256

### Compatibility

- `.sos` 語法與 Runtime Adapter API 未變更
- `ULCS-Intent-Request`／`ULCS-Intent-Bundle` 維持 v0.7
- LOG、Artifact、Manifest 與 Checkpoint 維持 v0.6
- v0.1–v0.7 工作流與 CLI 保持相容
- 既有 `ulcs` 可直接執行；`ulcs-approved` 是新增的受治理入口

### Safety

- HMAC 是共享密鑰完整性驗證，不是非對稱公鑰簽章或第三方不可否認證明
- Provider ID／model 是來源描述，不是密碼學身分
- Review digest 不自動包含未來的外部日誌、資料庫、HTTP 回應或其他 runtime input
- `ulcs-approved` 不是 OS 全域強制閘門；部署時仍須限制 Agent 直接存取 `ulcs`、Python 模組與底層執行器
- key distribution、rotation、revocation、檔案權限與秘密管理由部署環境負責

## 0.7.0 — 2026-07-23

### Added

- `ULCS-Intent-Request` v0.7 與 `ULCS-Intent-Bundle` v0.7
- review-first deterministic Intent Compiler
- `ready`／`needs_clarification`／`rejected` 三態
- `ulcs-intent` CLI 與 `--list-profiles`
- `log-analysis` profile：內嵌文字／本地檔案、明確 terms、regex 抽取與分類統計
- `http-json-fetch` profile：受限 GET／HEAD 與 origin-scoped network claim
- 自動生成 `workflow.sos`、Artifact Contract、enforce capability policy、Intent Plan 與 bundle metadata
- 候選產物回送既有 parser、graph、contract、policy validator
- confidence、assumptions、missing fields、risks、steps 與 validation report
- 原子 bundle 寫入
- v0.7 Intent Request 範例與 Intent Compiler 規格
- 跨平台生成後再執行的端到端 CI

### Compatibility

- `.sos` 語法未變更
- Runtime Adapter API 未變更
- LOG、Artifact、Manifest 與 Checkpoint 格式維持 v0.6
- v0.1–v0.6 工作流與 CLI 保持相容
- `ulcs` 與 `sos-mvp` 命令保留，新增獨立 `ulcs-intent`

### Safety

- Intent Compiler 不自動執行生成 bundle
- 未辨識意圖不猜測為任意程式，改回 `needs_clarification`
- 生成候選未通過既有 validator 時標記為 `rejected`
- capability policy 預設 enforce 並使用 exact inferred claims
- v0.7 不自動生成 POST、PUT、PATCH、DELETE、秘密注入或遠端修改
- 含空白的可攜式 PowerShell 路徑不被假裝為跨平台安全
- PowerShell path 與 filter 使用不插值的單引號 literal；內含單引號倍寫，NUL／換行直接拒絕
- 動態資源 path 不得標記為 `ready`，即使語言字串本身已安全引用
- confidence 不是正確率或安全證明
- Intent Request 與 Bundle 不應承載秘密

## 0.6.0 — 2026-07-23

### Added

- Artifact Contract sidecar 與節點 `input_schema`／`output_schema`／`persist_output`
- 受限 JSON Schema 驗證器
- 內容定址 Artifact Store 與 Artifact Reference
- Artifact digest、size、media type、encoding、path 與 schema digest
- `--contract`、`--artifact-mode`、`--artifact-dir`、`--artifact-threshold-bytes`
- 每個拓樸層完成後原子寫入 Execution Checkpoint
- `--checkpoint` 與 `--resume`
- 部分與完整 DAG 恢復
- Trace `resumed`、`artifacts`、`checkpoint_path`
- Manifest `artifact_digest` 與 `schema_digest`
- Artifact、Contract、Checkpoint／Resume 與 LOG v0.6 規格
- v0.6 工作流、contract、policy 與跨平台恢復驗證

### Compatibility

- `.sos` 表面語法未變更
- Artifact 預設 `off`，保留 v0.5 記憶體執行行為
- Runtime Adapter API 未變更
- 節點仍向下游傳遞普通 JSON 相容值，不傳遞 Artifact Reference
- v0.5 Manifest 可讀；新 Manifest 與 LOG 輸出版本為 0.6
- 快取、能力政策、配額、平行層、污染追蹤與重放語義保持相容

### Safety

- Artifact path 拒絕絕對路徑、`..` 與 Store 逃逸
- Artifact 讀取重新驗證 metadata、digest、size、schema digest 與值 schema
- Resume 要求 program／plan／policy digest 完全一致
- 節點 fingerprint 與 output digest 不一致時拒絕恢復
- Checkpoint 不保存完整值，完整輸出位於未加密 Artifact Store
- Checkpoint 不是交易回滾，不能撤銷已發生的外部副作用
- v0.6 schema 僅為文件所列子集，不宣稱完整 JSON Schema 相容

## 0.5.0 — 2026-07-22

### Added

- Canonical JSON 與 SHA-256 摘要
- `program_digest`、`plan_digest`、`policy_digest`
- 逐節點 input／output digest 與 execution fingerprint
- 內容定址快取及 `off`／`read`／`write`／`read-write` 模式
- `--cache-mode`、`--cache-dir`
- LOG v0.5 `deterministic` 與 `cacheable`
- Execution Manifest 與 `--emit-manifest`
- 重放驗證與 `--verify-manifest`
- Execution Trace 中的 fingerprints、digests、cache hits 與 manifest
- v0.5 快取／重放範例、規格與跨平台 CI 驗證

### Compatibility

- `.sos` 表面語法未變更
- 快取預設 `off`，保留 v0.4 執行行為
- `execute_program()` 仍回傳原始 outputs
- v0.4 resource policy、execution layers、taints 與 limits 保持相容
- 既有 Runtime 外掛未宣告 deterministic 時預設不可快取

### Safety

- 只有驗證計畫標記為 deterministic 且無外部資源效果的節點可快取
- 上游 canonical input digest 納入下游快取鍵
- 快取讀取會驗證格式、key 與 output digest；損壞項目視為 miss
- 快取項目含完整輸出且未加密，敏感工作流應關閉或隔離快取
- Manifest 不含完整 outputs，但尚未簽章，不能視為主機或 artifact attestation
- 重放驗證比較來源污染標籤，但不宣稱證明外部世界正確性

## 0.4.0 — 2026-07-22

### Added

- `capability@resource` 資源範圍政策
- 舊式未帶 `@` 能力規則的全資源相容語義
- LOG v0.4 `claims`、`taint_sources` 與 `execution_layers`
- 穩定拓樸分層與同層有界平行執行
- `max_nodes`、`max_workers`、`max_output_bytes`、`max_total_output_bytes`
- `--max-nodes`、`--max-workers`、`--max-output-bytes`、`--max-total-output-bytes`
- 動態資料污染傳播與 Execution Trace
- `--emit-trace`
- 資源政策、LOG v0.4 與 Execution Trace 規格
- 平行 DAG 與 scoped policy 範例

### Compatibility

- `.sos` 表面語法未變更
- 未指定 `max_workers` 時預設為 1，保留既有執行時序
- `effects` 與 `capabilities` 保留；政策新增使用 `claims`
- 沒有 `@` 的規則等價於 `capability@*`
- `execute_program()` 繼續回傳原始 outputs；追蹤功能由 `execute_program_with_trace()` 提供

### Safety

- 能力與節點數在第一個 Runtime 啟動前檢查
- 資料庫寫入與檔案寫入／刪除在平行層中保守序列化
- 輸出配額在值產生後以 UTF-8 JSON bytes 檢查，不宣稱具有交易回滾
- 污染標籤只追蹤來源，不自動去污或遮罩秘密
- 本版本仍不是作業系統沙箱

## 0.3.0 — 2026-07-22

### Added

- audit／enforce 能力政策
- `--policy`、`--allow`、`--deny`、`--enforce-capabilities`
- 完整 DAG 在第一個 Runtime 啟動前進行能力拒絕
- LOG v0.3 `capabilities` 欄位
- Bash、JavaScript、jq、HTTP 適配器
- Python `ulcs.adapters` entry point 外掛載入
- `--plugin module` 與 `ULCS_ADAPTER_MODULES`
- `--list-capabilities`
- HTTP localhost、`.local`、非公網 IP 與 redirect 驗證
- HTTP 2 MiB 回應限制與 `ULCS_HTTP_ALLOW_HOSTS`
- 四 Runtime 組合範例與 enforce 政策範例
- 能力政策、Runtime 外掛與 LOG v0.3 規格

### Compatibility

- 未指定政策時使用 audit 模式，保留 v0.2 執行行為
- `.sos` 表面語法與 DAG 結構保持相容
- `effects` 欄位保留，新增 `capabilities` 作為政策語義欄位
- `sos-mvp` CLI 名稱繼續保留

### Security

- 明確 deny 在 audit 與 enforce 模式都會拒絕
- enforce 模式要求每一項能力符合 allow
- 能力政策是 Runtime 啟動閘門，不宣稱為作業系統沙箱
- 外掛模組屬受信任 Python 程式碼

## 0.2.0 — 2026-07-22

### Added

- 多輸入語法：`from a, b.field as alias`
- 允許前向引用，並以穩定拓樸排序決定執行順序
- 未知節點、自我引用、重複引用與循環檢查
- LOG v0.2：顯式 edges、execution order 與 sinks
- Runtime 適配器註冊表與統一契約
- DAG 執行引擎及多 sink 最終結果
- `ulcs` CLI、`--output`、`--timeout`、`--list-languages`
- DAG 分支匯流範例
- Windows／Ubuntu、Python 3.11–3.13 CI

### Compatibility

- v0.1 的單一 `from node` 與線性 `.sos` 文件保持相容
- `sos-mvp` CLI 名稱保留為別名
- `Node.input_ref` 保留為第一個輸入的相容屬性

### Security

- 本版本仍是參考 Runtime，不是完整安全沙箱
- Python 隔離模式與靜態副作用掃描不可視為強安全邊界
