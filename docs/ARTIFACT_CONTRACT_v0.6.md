# ULCS Artifact Contract v0.6

## 1. Contract 文件

Artifact Contract 是獨立 JSON 文件，將 schema 與持久化要求套用到既有 `.sos` 節點，不改變 `.sos` 表面語法。

```json
{
  "format": "ULCS-Artifact-Contract",
  "version": "0.6",
  "nodes": {
    "summary": {
      "input_schema": {"type": "array"},
      "output_schema": {
        "type": "object",
        "required": ["count"],
        "properties": {
          "count": {"type": "integer"}
        }
      },
      "persist": true
    }
  }
}
```

Contract 不得引用不存在的節點。未列出的節點沒有 schema，且 `persist_output=false`。

## 2. 驗證時點

- `input_schema`：在 Runtime 啟動前驗證 resolved input；
- `output_schema`：Runtime、cache hit 或 checkpoint resume 產生值後驗證；
- `persist`：要求該節點輸出建立 Artifact，除非整體 Artifact 模式為 off；checkpoint 模式則強制持久化全部節點。

## 3. v0.6 Schema 子集

支援：

- `type`：`object`、`array`、`string`、`integer`、`number`、`boolean`、`null`；
- type union array；
- `enum`；
- `properties`；
- `required`；
- `additionalProperties: false`；
- `items`；
- `minLength`、`maxLength`；
- `minItems`、`maxItems`；
- `minProperties`、`maxProperties`。

不支援的 schema type 會被拒絕。v0.6 不宣稱完整實作 JSON Schema Draft 2020-12。

## 4. 摘要語義

Contract 內容會進入：

- `program_digest`；
- `plan_digest`；
- node execution fingerprint；
- Artifact `schema_digest`。

因此修改 schema 或 `persist` 會讓舊 checkpoint 失效；修改 input／output schema 也會改變節點快取鍵。

## 5. LOG v0.6 欄位

每個節點新增：

```json
{
  "input_schema": null,
  "output_schema": {"type": "object"},
  "persist_output": true
}
```

這些欄位描述驗證後計畫，不等同 Runtime 的靜態型別名稱。`output_type=Json` 與 `output_schema={...}` 可以同時存在：前者是 Runtime 類別契約，後者是實際值結構契約。
