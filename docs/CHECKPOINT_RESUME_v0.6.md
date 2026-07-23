# ULCS Checkpoint and Resume v0.6

## 1. Checkpoint 格式

```json
{
  "format": "ULCS-Execution-Checkpoint",
  "version": "0.6",
  "program_digest": "...",
  "plan_digest": "...",
  "policy_digest": "...",
  "nodes": {
    "node-id": {
      "fingerprint": "...",
      "input_digest": "...",
      "output_digest": "...",
      "taints": [],
      "artifact": {"format": "ULCS-Artifact", "version": "0.6"}
    }
  }
}
```

Checkpoint 不保存完整輸出；完整值位於 Artifact Store。

## 2. 寫入時點

啟用 `--checkpoint` 後，ULCS 在每個拓樸執行層完整成功後原子更新 checkpoint。若同一層中有任一節點失敗，該層不會被宣告完成。

## 3. Resume 條件

恢復前必須完全相符：

- program digest；
- validated plan digest；
- policy digest。

對每個已保存節點還必須相符：

- 依目前上游輸出重新計算的 node fingerprint；
- Artifact metadata、path、digest、size 與 schema digest；
- checkpoint output digest。

通過後，值才會被標記為 `resumed=true` 並送入下游。

## 4. 部分恢復

Checkpoint 可以只包含前幾個完成層。ULCS 會恢復可驗證節點，並從第一個未完成節點繼續執行。若同一層只保存部分節點，已保存節點可恢復，缺少的節點仍執行；下一層必須等待整層完成。

## 5. 與 Cache 的差異

Resume 只接受指定 checkpoint 中列出的產物，而且要求完整計畫與政策一致。Cache 則依節點 fingerprint 查找可重用結果，並只適用於 validated cacheable 節點。

`resumed` 與 `cache_hit` 是互斥來源標記：

- `RESUME`：來自 checkpoint Artifact；
- `CACHE`：來自內容定址快取；
- `RUNTIME`：實際執行語言適配器。

## 6. 非交易性邊界

Checkpoint 不是工作流交易日誌：

- 已發生的外部寫入不會回滾；
- 同一層失敗前可能已有 Runtime 產生外部副作用；
- 恢復非 deterministic 節點代表重用已保存輸出，不代表重新證明外部世界未改變；
- Artifact Store 遺失或篡改時恢復必須失敗。
