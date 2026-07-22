# ULCS Language Operator Graph（LOG）規格 v0.3

## 1. 與 v0.2 的關係

LOG v0.3 保留 v0.2 的 `nodes`、`edges`、`execution_order`、`sinks`、多輸入與穩定拓樸排序。主要新增是將節點副作用正式提升為能力政策介面。

## 2. 根結構

```json
{
  "format": "ULCS-Language-Operator-Graph",
  "version": "0.3",
  "nodes": [],
  "edges": [],
  "execution_order": [],
  "sinks": []
}
```

## 3. 節點

```json
{
  "node_id": "remote",
  "role": "source",
  "language": "http",
  "code": "https://example.com/data",
  "inputs": [],
  "input_type": "None",
  "output_type": "Json",
  "effects": ["network.access"],
  "capabilities": ["network.access"],
  "runtime": "python-urllib"
}
```

`effects` 為 v0.2 相容欄位；`capabilities` 是 v0.3 的政策判定欄位。兩者在 v0.3 參考 Runtime 中內容相同。

## 4. 能力判定

LOG 本身記錄能力需求，不內嵌最終允許結果。相同 LOG 可在不同環境套用不同政策：

```text
LOG + Capability Policy → ALLOW / AUDIT / DENY
```

政策結果屬執行環境狀態，不應污染可重現的程式中介表示。

## 5. Runtime 外掛

外掛節點與核心節點使用相同 Node schema。LOG 不需要知道適配器來自核心、entry point 或動態模組，只記錄 canonical language、runtime、型別與能力。

## 6. 相容要求

- v0.2 讀取器可忽略 `capabilities`。
- v0.3 讀取器應接受缺少 `capabilities` 的 v0.2 節點，並以 `effects` 回填。
- `.sos` 表面語法未因 v0.3 改變。
- v0.1 單一輸入與 v0.2 多輸入仍有效。

## 7. 未來擴充

後續可新增但不在 v0.3 實作範圍內：

- capability resource scope
- 資料敏感度與污染標籤
- Runtime 簽章與供應鏈資訊
- CPU、記憶體與輸出大小配額
- 平行執行層與 deterministic replay metadata
