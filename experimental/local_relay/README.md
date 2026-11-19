# Local relay (#1513)

預設關閉的本機優先路由策略：本機嘗試、65536 token 脈絡重試，再交由注入的 fallback。每階段都必須同時通過既有 acceptance 與 gate；帳務以注入 ledger 記錄。Gate adapter 應在 reply 附上 `input_tokens`、`output_tokens`、`usd`，供 ledger 記錄實際用量。以 `CAIRNWORK_LOCAL_RELAY=1` 啟用。

## 行為與已知限制
- 啟用條件：`CAIRNWORK_LOCAL_RELAY=1` 一定要設；`RelayConfig(enabled=True)` 單獨不能啟用。
- gate 丟例外 → 該次直接 `FAILED`，不重試、不進 fallback。
- 記帳：每次 worker 呼叫追加一列（成功記 token、usd=0；context 超限／逾時／不可達也記一列，token=0、usd=0）；每次 gate 回覆追加一列；fallback 成功回傳時追加一列。不寫列：acceptance 檢查、gate 例外、fallback 例外。ledger 寫入失敗 → `LEDGER_ERROR` 並停止。
- 環境變數 `CAIRNWORK_LOCAL_RELAY` 讀兩次：`RelayConfig.from_env()` 一次、`run_relay` 內再檢查一次，兩次都要是 `1`。
- 已知限制：ledger 路徑無上限（由呼叫端決定）、脈絡與逾時沒有範圍檢查、同一任務的多次嘗試不串成單一歷史記錄（各列獨立）、acceptance 失敗時仍會呼叫 gate（約 US$0.0004 每次，layer2 11/19 裁定不改）。
