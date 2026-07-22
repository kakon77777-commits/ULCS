# ULCS 能力政策規格 v0.3

## 1. 目的

ULCS 的語言節點可能啟動程序、讀寫檔案、存取資料庫或連線網路。v0.3 將原本僅供顯示的 `effects` 提升為可判定、可拒絕的能力政策輸入。

核心原則：

> 完整語言算子圖必須先通過能力判定，才可啟動第一個 Runtime。

因此，後段節點若缺乏權限，不會造成前段節點已經產生副作用的半完成狀態。

## 2. 政策模式

### 2.1 audit

- 未列入 `allow` 的能力會標記為 `AUDIT`。
- `AUDIT` 不阻止執行。
- 明確符合 `deny` 的能力仍會被拒絕。
- 未指定政策時採此模式，以保持 v0.2 執行相容性。

### 2.2 enforce

- 每一項節點能力都必須符合至少一個 `allow` 模式。
- 符合 `deny` 的能力一律拒絕。
- `deny` 優先於 `allow`。

## 3. JSON 政策格式

```json
{
  "mode": "enforce",
  "allow": [
    "python.execute",
    "process.execute",
    "database.read"
  ],
  "deny": [
    "network.*",
    "filesystem.delete"
  ]
}
```

`allow` 與 `deny` 支援 shell-style wildcard：

- `filesystem.*`
- `network.*`
- `*`

政策檔未寫 `mode` 時預設為 `enforce`；明確寫入 `audit` 時必須尊重該設定。

## 4. 判定優先序

對能力 $c$：

1. 若 $c$ 符合 `deny`，判定 `DENY`。
2. 否則若 $c$ 符合 `allow`，判定 `ALLOW`。
3. 否則若模式為 `enforce`，判定 `DENY`。
4. 否則判定 `AUDIT`。

純計算節點沒有能力需求，標記為 `pure`。

## 5. CLI

僅稽核：

```bash
ulcs examples/error_report.sos --dry-run
```

明確允許：

```bash
ulcs examples/error_report.sos \
  --enforce-capabilities \
  --allow filesystem.read \
  --allow process.execute \
  --allow python.execute \
  --allow database.* \
  --yes
```

使用政策檔：

```bash
ulcs examples/polyglot_v0.3.sos \
  --policy examples/capability_policy_v0.3.json \
  --yes --json
```

列出核心已知能力：

```bash
ulcs --list-capabilities
```

## 6. LOG v0.3

節點同時保留：

```json
{
  "effects": ["network.access"],
  "capabilities": ["network.access"]
}
```

`effects` 保留 v0.2 名稱；`capabilities` 明確表示該欄位已進入政策判定。

## 7. 目前邊界

v0.3 的能力推斷仍主要依賴適配器的靜態分析。這不等於作業系統級沙箱：

- Python、JavaScript、Bash 可能以動態方式取得未被關鍵字分析捕捉的能力。
- `filesystem.possible`、`network.possible` 是保守提示，不是完整資料流證明。
- 能力政策控制「是否允許啟動」，不能取代容器、seccomp、AppContainer、低權限帳戶或網路命名空間。
- 不應執行不可信 `.sos` 或不可信 Runtime 外掛。

後續版本應加入資源範圍，例如允許的目錄、網域、資料庫與最大 CPU／記憶體。
