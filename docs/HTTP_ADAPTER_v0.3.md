# ULCS HTTP 適配器 v0.3

HTTP 適配器將網路請求表示為一個需要 `network.access` 能力的語言節點。

## 表面語法

純 URL：

```sos
source remote = http{
https://example.com/data.json
}
```

JSON request specification：

```sos
source remote = http{
{
  "url": "https://example.com/data.json",
  "method": "GET",
  "headers": {"Accept": "application/json"}
}
}
```

非 GET／HEAD 且未提供 `body` 時，上游輸入會成為 request body。字典與列表會以 JSON 編碼。

## 回傳格式

```json
{
  "status": 200,
  "url": "https://example.com/data.json",
  "headers": {},
  "body": {}
}
```

若 body 是合法 JSON，會還原為 JSON value；否則保留文字。

## 防護

- 僅允許 `http` 與 `https`
- 拒絕 localhost 與 `.local`
- DNS 解析結果必須是公網 IP
- redirect 目標重新驗證
- 回應上限 2 MiB
- 可用 `ULCS_HTTP_ALLOW_HOSTS` 進一步限制 hostname wildcard
- 必須由能力政策允許 `network.access`

## 邊界

這些防護不能取代企業代理、容器網路、DNS pinning、出站防火牆與完整 SSRF 防禦。高風險環境應在隔離網路內執行。
