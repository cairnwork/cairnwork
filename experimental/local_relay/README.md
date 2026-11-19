# Local relay (#1513)

預設關閉的本機優先路由策略：首次以 32768 token 脈絡嘗試、再以 65536 token 重試，最後交由現有 AgyWorker fallback。每階段都必須同時通過 Core acceptance 與 Haiku gate；帳務寫入 `~/.local/state/cairnwork-local-relay/ledger.jsonl`，可用 `--ledger` 改路徑。Gate adapter 由 transport usage 換算 `input_tokens`、`output_tokens`、`usd`，供 ledger 記錄實際用量；Agy fallback 的 `quota_units` 獨立記錄。以 `CAIRNWORK_LOCAL_RELAY=1` 啟用。外部 worker、Core verifier、gate transport、fallback 執行器及 ledger 均可注入；測試使用 fake transport／worker，不呼叫真端點或模型。

`python -m experimental.local_relay.cli --state DIR --repo OWNER/NAME --clone PATH run-once` 沿用 Issue intake 的參數形式。呼叫端須注入 relay runtime；缺少 runtime 會明確拒絕，不會退回既有 Agy-only run-once。

## 行為與已知限制
- 啟用條件：`CAIRNWORK_LOCAL_RELAY=1` 一定要設；`RelayConfig(enabled=True)` 單獨不能啟用。
- gate 丟例外 → 該次直接 `FAILED`，不重試、不進 fallback。
- 記帳：每次 worker 呼叫追加一列（成功記 token、usd=0；context 超限／逾時／不可達也記一列，token=0、usd=0）；每次 gate 回覆追加一列；fallback 成功回傳時追加一列。不寫列：acceptance 檢查、gate 例外、fallback 例外。ledger 寫入失敗 → `LEDGER_ERROR` 並停止。
- 環境變數 `CAIRNWORK_LOCAL_RELAY` 讀兩次：`RelayConfig.from_env()` 一次、`run_relay` 內再檢查一次，兩次都要是 `1`。
- 已知限制：ledger 路徑無上限（由呼叫端決定）、脈絡與逾時沒有範圍檢查、同一任務的多次嘗試不串成單一歷史記錄（各列獨立）、acceptance 失敗時仍會呼叫 gate（約 US$0.0004 每次，layer2 11/19 裁定不改）。

## 跟實驗的差異

實驗交雲端是 Haiku 直做；正式流程的 fallback 是 Agy（Gemini）。因此實驗的省錢結論不能直接套用到正式流程；只有在 Cairnwork 自己的對稱 A/B 中量到結果才算數。
