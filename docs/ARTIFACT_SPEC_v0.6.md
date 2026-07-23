# ULCS Artifact Specification v0.6

## 1. 目的

Artifact 是節點輸出的可驗證持久化投影。Runtime 與下游節點仍接收原始 JSON 相容值；Artifact Reference 只屬於執行、追蹤、快取、checkpoint 與恢復層。

## 2. Artifact Reference

```json
{
  "format": "ULCS-Artifact",
  "version": "0.6",
  "digest": "<content-sha256>",
  "media_type": "application/json",
  "encoding": "utf-8",
  "size": 123,
  "path": "objects/ab/<content-digest>.<schema-digest-or-no-schema>.json",
  "schema_digest": "<sha256-or-null>"
}
```

`digest` 是 canonical JSON UTF-8 bytes 的 SHA-256。`size` 也是同一 bytes 序列的長度。`schema_digest` 固定產物被驗證時使用的 output schema。

## 3. 儲存布局

```text
<artifact-dir>/
  objects/
    <content-digest-prefix>/
      <content-digest>.<schema-digest-or-no-schema>.json
```

物件不可由節點名稱定位；相同內容與相同 schema 可共用物件。相同內容若使用不同 schema，內容 digest 相同，但 schema digest 與物件路徑不同，避免契約 metadata 互相覆蓋。寫入使用同目錄暫存檔與原子 replace。

## 4. 驗證

讀取 Artifact 時必須驗證：

1. format 與 version；
2. reference 路徑不得為絕對路徑或包含 `..`；
3. 儲存內容與 reference metadata 相同；
4. canonical JSON digest；
5. canonical JSON size；
6. schema digest；
7. 實際值再次通過 schema。

任一項失敗都不得將值送入下游 Runtime。

## 5. 持久化模式

CLI 提供：

- `off`：不建立 Artifact；
- `auto`：達到大小門檻或 contract 明示 `persist` 時建立；
- `all`：每個完成節點都建立。

checkpoint／resume 會強制 `persist_all`，因為只保存摘要而不保存值的 checkpoint 無法支援恢復。

輸出必須先通過單節點與累積輸出配額，才可以寫入 Artifact Store。配額拒絕的輸出不應留下新的 Artifact。

## 6. 與快取的差異

快取回答「相同節點與輸入能否省略重算」；Artifact 回答「某個已產生的值是否可持久化、驗證與引用」。

- 非 deterministic 節點可以產生 Artifact；
- 非 cacheable 節點可以從 checkpoint 恢復，但只能使用同一次相容計畫留下的 Artifact；
- Artifact 存在不代表一般執行可任意跳過 Runtime。

## 7. 安全邊界

Artifact Store 目前未加密、未簽章，也沒有遠端物件儲存的存取控制。它不應保存未經治理的秘密。部署環境仍應使用檔案權限、隔離磁碟、秘密管理與生命週期清理。
