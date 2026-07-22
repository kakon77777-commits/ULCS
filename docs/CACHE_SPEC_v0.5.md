# ULCS Content-Addressed Cache Specification v0.5

## 1. 目的

ULCS v0.5 的快取不是一般的「節點名稱快取」，而是以節點定義、驗證後 Runtime 計畫與實際輸入內容共同形成內容定址鍵。目標是避免把不同輸入、不同 Runtime 或不同權限範圍的執行結果誤認為同一結果。

快取預設關閉。只有驗證計畫明確標記為 `deterministic=true` 且 `cacheable=true` 的節點可以讀寫快取。

## 2. 快取模式

- `off`：不讀、不寫。
- `read`：只讀取既有項目，未命中時執行 Runtime，但不寫入。
- `write`：不讀取既有項目，執行後寫入。
- `read-write`：先讀取；未命中時執行並寫入。

CLI：

```bash
ulcs workflow.sos \
  --cache-mode read-write \
  --cache-dir .ulcs-cache \
  --yes
```

## 3. 節點資格

v0.5 的內建保守規則：

1. Runtime 適配器必須宣告或被核心辨識為 deterministic。
2. 節點不可宣告 `filesystem.*`、`network.*`、`database.*` 等外部資源效果。
3. 節點不可宣告 `process.spawn.possible`。

目前內建可快取類型主要是純 Regex 與 jq filter。Python、JavaScript、Bash、PowerShell、HTTP 與 SQLite 預設不快取。

第三方適配器可以提供 `deterministic = True`，但該聲明屬於受信任外掛契約的一部分。錯誤聲明可能造成不正確快取。

## 4. 快取鍵

對節點 $n$，先計算輸入摘要：

$$
D_{in}(n)=\operatorname{SHA256}(\operatorname{CJSON}(input_n))
$$

再計算節點執行指紋：

$$
K_n=\operatorname{SHA256}(\operatorname{CJSON}(N_n,R_n,C_n,D_{in}(n)))
$$

其中：

- $N_n$：節點 ID、角色、語言、原始程式碼、輸入／輸出型別。
- $R_n$：驗證後 Runtime identity。
- $C_n$：資源範圍 claims 與 deterministic 聲明。
- $D_{in}(n)$：實際輸入內容摘要。

因此：

- 程式碼改變會失效。
- Runtime identity 改變會失效。
- 資源範圍改變會失效。
- 上游輸出改變會使下游快取鍵失效。

節點 ID 目前也是指紋的一部分；重新命名節點會產生新快取鍵。

## 5. Canonical JSON

摘要使用 UTF-8 Canonical JSON：

- object keys 排序。
- 無多餘空白。
- tuple 視為 list。
- Path 視為字串。
- 非 JSON 原生值使用穩定字串表示。
- 不允許 NaN／Infinity。

v0.5 並未宣稱可對任意 Python object 保持本體等價；Runtime 輸出仍應以 JSON-compatible value 為主。

## 6. 儲存格式

快取路徑：

```text
<cache-dir>/<key[0:2]>/<key>.json
```

項目格式：

```json
{
  "format": "ULCS-Content-Addressed-Cache",
  "version": "0.5",
  "key": "<sha256>",
  "output_digest": "<sha256>",
  "value": {}
}
```

寫入採同目錄暫存檔、flush、`fsync` 與 atomic replace。讀取時會重新計算 `output_digest`；格式錯誤、JSON 損壞或摘要不符都視為 cache miss，而不是信任損壞資料。

## 7. 並行語義

不同指紋可並行讀寫。相同指紋同時寫入時，最後一次 atomic replace 應產生相同內容；v0.5 不提供跨程序鎖與分散式鎖。

快取命中不改變 DAG 拓樸、污染傳播、輸出配額或 manifest 驗證。命中值仍需通過輸出 bytes 配額。

## 8. 安全與隱私

快取項目包含完整節點輸出，可能包含個資、秘密或受限資料。v0.5：

- 不加密快取。
- 不自動遮罩污染資料。
- 不提供 TTL 或淘汰策略。
- 不提供多租戶隔離。
- 不驗證外部簽章。

正式部署應將快取目錄放在低權限、加密、具生命週期政策的儲存空間中。對敏感資料應保持 `cache-mode=off`，或在更高層政策中禁止快取。

## 9. 非目標

v0.5 快取不能證明 Runtime 本身是純函數，也不能取代容器、可重現建置、依賴鎖定、環境快照或遠端 artifact attestation。
