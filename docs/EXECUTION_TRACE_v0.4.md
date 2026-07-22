# ULCS Execution Trace v0.4

## 1. 目的

LOG 描述可執行計算圖；Execution Trace 描述一次具體執行產生的結果、資料污染與輸出配額消耗。

CLI：

```bash
ulcs workflow.sos \
  --policy policy.json \
  --yes \
  --emit-trace output/trace.json
```

## 2. 結構

```json
{
  "outputs": {
    "remote": {"value": 1},
    "derived": {"value": 2}
  },
  "taints": {
    "remote": ["external.network:https://api.example.com"],
    "derived": ["external.network:https://api.example.com"]
  },
  "output_bytes": {
    "remote": 11,
    "derived": 11
  },
  "total_output_bytes": 22,
  "execution_layers": [
    ["remote"],
    ["derived"]
  ]
}
```

## 3. 污染傳播

節點的動態污染集合為：

```text
自身 taint_sources ∪ 所有直接依賴節點的動態 taints
```

由於依賴污染已包含其上游集合，標籤會沿 DAG 傳遞至所有後代節點。

v0.4 不會自動移除污染。資料驗證、清洗、摘要或型別轉換不等於安全去污。未來版本可以加入具證明義務的 sanitizer operator；在此之前，所有衍生資料繼承來源標籤。

## 4. 輸出計量

輸出先以緊湊 JSON、UTF-8 與 `default=str` 序列化，再計算 bytes。這個計量對跨平台執行保持穩定，但不等於 Runtime 的峰值記憶體，也不包含 stderr、程序內部記憶體或外部檔案大小。

## 5. 平行執行

同一 execution layer 的工作可以同時執行，但 Trace 中的 outputs、taints 與 output_bytes 仍依穩定節點順序寫入。這使計算時間可以平行化，同時保持可重現的紀錄順序。

資料庫寫入與檔案寫入／刪除節點使用保守的共用序列鎖。這不是完整資源鎖圖；外掛若操作其他共享資源，仍應自行提供同步或在政策中把 `max_workers` 設為 1。

## 6. 邊界

Execution Trace 是可觀察性與治理資料，不是秘密資料的自動遮罩，也不是事件溯源資料庫。Trace 可能包含工作流的完整輸出；正式環境應控制其儲存位置、存取權、保留期限與加密方式。
