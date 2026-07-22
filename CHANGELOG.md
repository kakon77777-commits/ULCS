# Changelog

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
