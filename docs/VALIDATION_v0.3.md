# ULCS v0.3 驗證紀錄

**日期**：2026-07-22  
**範圍**：能力政策、Runtime 外掛、Bash／JavaScript／jq／HTTP 適配器

## 本地原型驗證

在隔離測試骨架中完成：

- 7 項能力政策與外掛註冊測試通過
- enforce 模式拒絕未列入 allow 的能力
- wildcard allow 可授權 `python.*`
- audit 模式中的明確 deny 仍會拒絕
- 政策檔明確指定 audit 時不被覆蓋
- LOG v0.3 輸出 capabilities
- HTTP JSON request specification 可解析

實際 Runtime 驗證：

- Bash 輸出 `{"sum": 3}` 並還原為 JSON object
- Node.js 計算輸出 `{"sum": 3}`
- jq 計算輸出 `7`

## 倉庫測試新增

- `tests/test_capabilities.py`
  - 完整 DAG 在任何 Runtime 啟動前拒絕
  - enforce、audit、allow、deny 與 wildcard
  - LOG v0.3 capabilities
- `tests/test_extensions.py`
  - 內建擴充語言註冊
  - 模組外掛契約
  - HTTP localhost 拒絕
  - Bash、Node.js、jq 實際執行；缺少 Runtime 時明確 skip

## 遠端 CI

GitHub Actions 設定為：

- Ubuntu、Windows
- Python 3.11、3.12、3.13
- 全部 unittest
- 語言與能力列舉
- v0.2 DAG dry-run
- v0.3 enforce 政策與四 Runtime 計算圖 dry-run

遠端 CI 是否通過，必須以 PR 與 Actions 實際回報為準；此文件不預先宣稱通過。

## 尚未驗證／尚未完成

- HTTP 對所有 DNS rebinding、代理與企業網路情境的完整防護
- 作業系統級沙箱
- capability resource scope
- 外掛簽章與供應鏈驗證
- 平行 DAG 排程
- 跨 Runtime 資源配額
