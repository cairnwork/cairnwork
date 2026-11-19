# Local relay (#1513)

預設關閉的本機優先路由策略：首次以 32768 token 脈絡嘗試、再以 65536 token 重試，最後交由現有 AgyWorker fallback。每階段都必須同時通過 Core acceptance 與 Haiku gate；帳務寫入 `~/.local/state/cairnwork-local-relay/ledger.jsonl`，可用 `--ledger` 改路徑。Gate adapter 由 transport usage 換算 `input_tokens`、`output_tokens`、`usd`，供 ledger 記錄實際用量；Agy fallback 的 `quota_units` 獨立記錄。以 `CAIRNWORK_LOCAL_RELAY=1` 啟用。外部 worker、Core verifier、gate transport、fallback 執行器及 ledger 均可注入；測試使用 fake transport／worker，不呼叫真端點或模型。

`python -m experimental.local_relay.cli --state DIR --repo OWNER/NAME --clone PATH --canary-report PATH run-once` 使用預設組裝：本機 worker `qwen3.5-9b` 連 `http://127.0.0.1:18101/v1`，Core acceptance 使用 PAUSE `CoreJudge` 與同一 state 中的 verifier 設定，Haiku gate 使用 `~/.local/bin/claude-api-run --tag cw-local-relay`，最後 fallback 到正式 `AgyWorker`。`--ledger` 可覆寫 JSONL 帳本路徑。測試仍可透過 `main(..., run_once=...)` 注入假 runtime。啟用前必須提供有效的 worker canary report；報告與 `.sha256` sidecar 會複製到該次 episode 的輸出目錄供 local_worker 核對。

`--dry-route` 只印路徑設定與旗標狀態，不建立 runtime、不連服務，也不建立 ledger。它顯示 local context `32768 → 65536`、gate transport、Agy fallback 和 ledger 路徑。

`--fixture DIR` 可略過 GitHub，從 repo 根目錄的 `cairnwork-local-relay.json` 讀一題，需包含 `task_id`、`prompt`、`allowed_files`（repo 相對路徑陣列）、`verifiers`（每項為受限 pytest argv 陣列），可選 `title`。Fixture 使用該 repo 的目前 `HEAD` 當 base，透過相同的 local worker、CoreJudge、Haiku gate 與 Agy fallback；傳入的 `--state` 仍須是已初始化且處於 accepting 狀態的 Cairnwork state，CoreJudge 會使用該 state 的 verifier 設定。Fixture 不會建立 Cairnwork intake ownership binding，也不會驗證 Issue 的 queue trust、遠端 base 或 publish/settle 流程。因此 fixture 冒煙只驗 relay runtime 路徑，不等於完整 Issue intake 演練。

## 行為與已知限制
- 啟用條件：`CAIRNWORK_LOCAL_RELAY=1` 一定要設；`RelayConfig(enabled=True)` 單獨不能啟用。
- gate 丟例外 → 該次直接 `FAILED`，不重試、不進 fallback。
- 記帳：每次 worker 呼叫追加一列（成功記 token、usd=0；context 超限／逾時／不可達也記一列，token=0、usd=0）；每次 gate 回覆追加一列；fallback 成功回傳時追加一列。不寫列：acceptance 檢查、gate 例外、fallback 例外。ledger 寫入失敗 → `LEDGER_ERROR` 並停止。
- 環境變數 `CAIRNWORK_LOCAL_RELAY` 讀兩次：`RelayConfig.from_env()` 一次、`run_relay` 內再檢查一次，兩次都要是 `1`。
- CLI 旗標未開時在 runtime 組裝前拒跑；dry-route 是唯讀診斷，會如實列出旗標目前狀態。
- 已知限制：ledger 路徑無上限（由呼叫端決定）、脈絡與逾時沒有範圍檢查、同一任務的多次嘗試不串成單一歷史記錄（各列獨立）、acceptance 失敗時仍會呼叫 gate（約 US$0.0004 每次，layer2 11/19 裁定不改）。

## 跟實驗的差異

實驗交雲端是 Haiku 直做；正式流程的 fallback 是 Agy（Gemini）。因此實驗的省錢結論不能直接套用到正式流程；只有在 Cairnwork 自己的對稱 A/B 中量到結果才算數。
