# ULCS Resource Policy v0.4

## 1. 目的

v0.3 的能力政策回答「節點是否可使用某項能力」。v0.4 將判定單位提升為：

```text
capability@resource
```

例如：

```text
network.access@https://api.example.com
filesystem.read@./data/*.json
python.execute@runtime://python
database.write@sqlite://workflow
```

政策因此可以允許某個 API 主機，同時拒絕其他網路目標；也可以限制檔案能力只作用於指定路徑模式。

## 2. 相容性

沒有 `@` 的 v0.3 規則會被解讀為該能力對所有資源有效：

```text
network.access
```

等價於：

```text
network.access@*
```

因此既有政策檔不必修改。

## 3. 規則格式

```json
{
  "mode": "enforce",
  "allow": [
    "network.access@https://api.example.com",
    "python.execute@runtime://python",
    "filesystem.read@./data/*"
  ],
  "deny": [
    "network.access@http://*",
    "filesystem.delete@*"
  ]
}
```

`capability` 與 `resource` 兩側都使用 shell-style wildcard。判定順序維持：

1. `deny`
2. `allow`
3. enforce 模式中的未授權 claim
4. audit 模式中的稽核 claim

## 4. Claim 推斷

現有 Runtime 仍宣告粗粒度 `effects`。v0.4 分析層再從語言、程式文字與 effects 推導 `claims`。

可靜態辨認的 URL、主機、常見路徑與 Runtime 會形成具體資源；無法在執行前確定的動態資源使用 `*`。`*` 不是無風險，而是表示政策必須明確接受未知範圍。

目前標準資源形式：

- `https://host[:port]`
- `http://host[:port]`
- 正規化為 `/` 的檔案路徑或 wildcard
- `sqlite://workflow`
- `runtime://python`
- `runtime://node`
- `runtime://bash`
- `runtime://powershell`
- `runtime://jq`
- `runtime://child-process`

靜態分析屬保守提示與政策閘門，不是完整程式證明。動態組合路徑、反射、外部程式與外掛仍可能要求作業系統沙箱。

## 5. 執行配額

政策可加入：

```json
{
  "limits": {
    "max_nodes": 256,
    "max_workers": 4,
    "max_output_bytes": 8388608,
    "max_total_output_bytes": 33554432
  }
}
```

- `max_nodes`：Runtime 啟動前檢查。
- `max_workers`：每個拓樸層的最大工作者數；預設為 1。
- `max_output_bytes`：單節點 JSON UTF-8 輸出上限。
- `max_total_output_bytes`：工作流累積 JSON UTF-8 輸出上限。

節點數可以預先拒絕；輸出大小只能在 Runtime 產生資料後判定。平行層中的其他已啟動節點可能在輸出超限被發現前完成，因此輸出配額不是交易回滾機制。

CLI 可覆寫政策配額：

```bash
ulcs workflow.sos \
  --policy policy.json \
  --max-workers 4 \
  --max-output-bytes 1048576 \
  --yes
```

## 6. 安全邊界

Resource Policy 是執行前授權與執行期配額層，不是 OS sandbox。正式部署仍應使用低權限帳戶、容器、唯讀掛載、網路代理、程序限制及外掛簽章。
