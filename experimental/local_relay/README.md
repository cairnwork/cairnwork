# Local relay (#1513)

預設關閉的本機優先路由策略：本機 worker 先用 32768 token，接著以 65536 token 重試；兩次都未得到可接受結果時，fallback 仍使用 local_worker agent loop，端點改為 loopback Haiku proxy，模型為 `claude-haiku-5-5`、context cap 為 65536。各候選都要通過 Core acceptance 與 Haiku gate 才能成為成功路由。帳務寫入 `~/.local/state/cairnwork-local-relay/ledger.jsonl`，可用 `--ledger` 改路徑。以 `CAIRNWORK_LOCAL_RELAY=1` 啟用。

人員需先啟動外部 proxy；relay 不負責啟停，也不包含 proxy 程式碼：

```sh
python3 haiku_proxy.py --port <PORT> --logdir <DIR> --tag cw-local-relay
```

`--haiku-proxy-url` 預設為 `http://127.0.0.1:18301/v1`，必須是 `127.0.0.1` 或 `[::1]` 的明確 loopback URL。fallback 以 `/ab/label` 設定任務費用標籤，經同一個 local_worker loop 執行後設為 `idle`，再讀 `/ab/stats?label=...`。只有取得有效正成本、沒有 budget stop 或 model mismatch，才會記錄 fallback 的 `cost_usd`、input/output tokens；成本或 stats 不明即視為失敗。fallback 產生的候選仍需 acceptance 和 gate 通過才會回報 `FALLBACK`。

Proxy 提供 `GET /v1/models`、`POST /v1/chat/completions`、`POST /apply-template`、`POST /tokenize`，以及上述 `/ab/label` 和 `/ab/stats` 成本標記端點。

`python -m experimental.local_relay.cli --state DIR --repo OWNER/NAME --clone PATH --canary-report PATH run-once` 使用本機 `qwen3.5-9b` 與 `http://127.0.0.1:18101/v1`，Core acceptance 使用 PAUSE `CoreJudge` 與相同 state 的 verifier 設定，Haiku gate 使用 `~/.local/bin/claude-api-run --tag cw-local-relay`。啟用前必須提供有效的 worker canary report；報告和 `.sha256` sidecar 會複製到該次 episode 輸出目錄供 local_worker 核對。

`--dry-route` 只印設定和旗標，不組裝 runtime、不連網，也不建立 ledger。它列出本機與 fallback endpoint、模型和脈絡上限。

`--fixture DIR` 可略過 GitHub，從 repo 根目錄的 `cairnwork-local-relay.json` 讀一題，需包含 `task_id`、`prompt`、`allowed_files`（repo 相對路徑陣列）、`verifiers`（argv 陣列，例如 `["python3", "-m", "pytest", "tests/test_example.py"]`），可選 `title`。Fixture 使用該 repo 的目前 `HEAD` 當 base，並走相同 local worker、CoreJudge、gate 與 proxy fallback。傳入的 `--state` 仍須是已初始化且 accepting 的 Cairnwork state；fixture 不建立 intake ownership binding，也不驗證 Issue queue trust、遠端 base 或 publish/settle 流程。

Run root 預設建立在 local_worker 的 `INSTALLED_RUNS`（`/Users/Shared/cairnwork-worker/local-runs`），必須是 canonical runs 目錄的直接子目錄、名稱為 32 位小寫 hex，root 為 0750，並由安裝設定提供 `_cairnwork_wrun` 群組及 setgid 繼承。`--runs DIR` 可覆寫 runs 目錄，但仍須符合 local-worker-tool 的 canonical 路徑和權限契約。

Relay 以 PAUSE `WorkerRunner` 共用的 `_prepare` 建 checkout：no-hardlinks clone、detached base checkout、hardened git 設定、依來源 checkout 複製檔案模式與群組，並核對乾淨狀態和 base SHA。tmp_path 測試確認 mode、group 和 `git status --porcelain`；測試不使用 sudo，無法重現安裝環境中 worker uid 的真實屬主與 sudo 拒讀行為。

## 行為與已知限制

- 啟用條件：`CAIRNWORK_LOCAL_RELAY=1`；`RelayConfig(enabled=True)` 單獨不能啟用。
- gate 丟例外或 fallback 成本無法確認時，該次失敗，不回報成功。
- 每次 worker 和 gate 回覆各自寫 ledger；fallback 僅在有有效 proxy stats 後記錄成本。ledger 寫入失敗會停止路由。
- acceptance 失敗時仍會呼叫 gate（約 US$0.0004 每次，layer2 11/19 裁定不改）。

## 跟實驗的差異

Fallback 與實驗 G1 H 臂同一路徑，差異只在是否經過 acceptance＋gate 的 relay 流程。
