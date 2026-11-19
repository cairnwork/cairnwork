# Local relay (#1513)

預設關閉的本機優先路由策略：本機嘗試、65536 token 脈絡重試，再交由注入的 fallback。每階段都必須同時通過既有 acceptance 與 gate；帳務以注入 ledger 記錄。Gate adapter 應在 reply 附上 `input_tokens`、`output_tokens`、`usd`，供 ledger 記錄實際用量。以 `CAIRNWORK_LOCAL_RELAY=1` 啟用。
