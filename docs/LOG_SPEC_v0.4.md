# ULCS Language Operator Graph Specification v0.4

## 1. 格式識別

```json
{
  "format": "ULCS-Language-Operator-Graph",
  "version": "0.4"
}
```

v0.4 保留 v0.3 的 nodes、edges、execution_order、sinks、effects、capabilities 與 runtime，新增 `execution_layers`、`claims` 與 `taint_sources`。

## 2. execution_layers

```json
{
  "execution_layers": [
    ["left", "right"],
    ["merged"]
  ]
}
```

每一層中的節點互不依賴，可以在 `max_workers > 1` 時並行。層與層之間具有完整 barrier：後一層不會在前一層完成前啟動。

`execution_order` 仍存在，並以原始文件順序穩定地展平所有 execution layers，供需要線性表示的工具使用。

## 3. claims

每個節點新增：

```json
{
  "claims": [
    {
      "capability": "network.access",
      "resource": "https://api.example.com"
    }
  ]
}
```

`effects` 與 `capabilities` 繼續輸出粗粒度能力名稱；`claims` 是 v0.4 政策實際判定的能力—資源對。

若資源無法靜態決定，`resource` 為 `*`。

## 4. taint_sources

```json
{
  "taint_sources": [
    "external.network:https://api.example.com"
  ]
}
```

這個欄位只描述節點自身可能引入的資料來源，不包含上游傳播結果。完整動態傳播結果位於 Execution Trace。

標準標籤包括：

- `external.network:<resource>`
- `external.filesystem:<resource>`
- `external.database:<resource>`
- `potential.network`
- `potential.filesystem`

## 5. 節點範例

```json
{
  "node_id": "remote",
  "role": "source",
  "language": "http",
  "code": "https://api.example.com/data",
  "inputs": [],
  "input_type": "None",
  "output_type": "Json",
  "effects": ["network.access"],
  "capabilities": ["network.access"],
  "runtime": "python-urllib",
  "claims": [
    {
      "capability": "network.access",
      "resource": "https://api.example.com"
    }
  ],
  "taint_sources": [
    "external.network:https://api.example.com"
  ]
}
```

## 6. 相容性

v0.3 消費者可以忽略未知欄位，但必須注意 `version` 已升為 `0.4`。需要平行排程或資源政策的消費者不得只讀取 `execution_order` 或 `capabilities`，應同時處理 `execution_layers` 與 `claims`。
