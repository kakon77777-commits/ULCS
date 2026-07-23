# ULCS v0.8 Release Validation Record

版本：0.8.0  
實作合併提交：`32e0768d90017e3df79a8a84552bf45b686ad96e`  
日期：2026-07-23

## 驗證目的

本文件記錄 ULCS v0.8 發布候選的跨平台驗證範圍。它不取代 GitHub Actions 結果，也不對尚未執行的測試預先宣告成功。

## 必須通過的矩陣

```text
Ubuntu  × Python 3.11
Ubuntu  × Python 3.12
Ubuntu  × Python 3.13
Windows × Python 3.11
Windows × Python 3.12
Windows × Python 3.13
```

## 必須通過的鏈路

1. 安裝套件並執行完整單元測試。
2. 驗證 v0.1–v0.7 的既有相容路徑。
3. 讀取 `ULCS-Intent-Provider-Proposal` v0.8。
4. 拒絕 Provider 自行提供 workflow、policy、status、ready、approval 或 signature。
5. 經 deterministic Intent Compiler 與既有 parser、DAG、Artifact Contract、capability policy validator 產生 ready bundle。
6. 建立固定檔案集合、逐檔 SHA-256／size 與 canonical Review Bundle digest。
7. 建立綁定同一 bundle digest、`approve` 決策與 `execute` scope 的 HMAC-SHA256 Approval Record。
8. 驗證錯誤 key、reject 決策、缺少 scope、檔案篡改與治理參數覆寫皆被拒絕。
9. Approved Runner 將已驗證檔案複製到暫存快照並重新比對 size 與 SHA-256。
10. 快照交回既有 ULCS runtime 執行，結果必須得到 `ERROR=2`、`FATAL=1`、總數 `3`。

## Windows UTF-8 回歸

早期矩陣中，Windows 三個 Python 版本的單元測試本身均成功，但 CI 在將含中文的 `test-output.txt` 以預設主控台編碼列印時失敗。發布候選改為：

- job 級 `PYTHONIOENCODING=utf-8`
- 以 `sys.stdout.buffer` 寫出明確 UTF-8 bytes

此修正只影響 CI 診斷輸出，不變更 ULCS runtime、Intent Compiler、Provider Contract 或 Approval Gate 語義。

## 邊界

- HMAC-SHA256 是共享密鑰完整性驗證，不是非對稱公鑰簽章或不可否認證明。
- `ulcs-approved` 是受治理入口，不是作業系統層的全域強制閘門。
- Review Bundle 摘要生成與治理檔案，不自動固定未來的外部 runtime inputs。
- 完整部署仍須使用低權限帳戶、容器、網路與檔案系統隔離、秘密管理及 key rotation。
