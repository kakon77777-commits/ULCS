# ULCS Language Operator Graph（LOG）規格 v0.2

## 1. 目的

LOG 是 `.sos` 表面語法與各語言 Runtime 之間的共同中介表示。它不試圖統一語言內部語法，而只統一跨語言組合所必須的結構：

- 節點身份與語言域
- 輸入引用
- 型別邊界
- Runtime
- 副作用
- 執行依賴
- Sink 輸出

## 2. 節點

每個節點至少包含：

```json
{
  "node_id": "report",
  "role": "transform",
  "language": "py",
  "code": "result = ...",
  "inputs": [
    {"node_id": "errors", "field": null, "alias": "counts"}
  ],
  "input_type": "InputMap",
  "output_type": "Json",
  "effects": ["python.execute"],
  "runtime": "python-isolated-subprocess"
}
```

## 3. 邊

每個輸入引用投影為一條邊：

```json
{
  "from": "errors",
  "from_field": null,
  "to": "report",
  "input_key": "counts"
}
```

`input_key` 由 `as` 別名決定；若未提供別名，使用來源節點名稱。

## 4. 多輸入語義

單一輸入保持 v0.1 行為，Runtime 直接收到來源值。

多輸入時，Runtime 收到映射：

```json
{
  "counts": {"ERROR": 3},
  "meta": {"version": "0.2"}
}
```

## 5. DAG 約束

LOG 必須符合：

1. 節點 ID 唯一。
2. 所有輸入來源存在。
3. 節點不可引用自身。
4. 同一節點不可重複引用相同來源。
5. 圖必須無循環。
6. 執行順序由穩定拓樸排序產生。

## 6. 型別

v0.2 的共同邊界型別為：

- `None`
- `Any`
- `Text`
- `FileList`
- `MatchList`
- `Json`
- `Table`
- `InputMap`

`InputMap` 表示兩個以上具名來源的組合輸入。這是橋接型別，不是任何底層語言的原生型別。

## 7. 適配器契約

每個語言適配器必須宣告：

- canonical language name
- aliases
- accepted input types
- output type
- runtime identification
- static effect analysis
- execute operation

新增語言時，不應修改解析器或 LOG 模型；只需註冊新的適配器。

## 8. 執行

v0.2 依穩定拓樸順序執行 DAG。獨立分支已能被正確表示與排序，但參考 Runtime 暫時採順序執行，以維持副作用可預測性。後續可在不修改 LOG 格式的前提下加入分層平行排程。
