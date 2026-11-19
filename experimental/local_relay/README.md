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

## 與 canary 的差異

| 項目 | `canary.py --mode worker` | Relay worker episode | 結果／處理 |
|---|---|---|---|
| `--runs` | 預設 `INSTALLED_RUNS`，或解析傳入的 `--runs`；run root 是其直接子目錄。 | `_local_runs` 同樣解析傳入路徑，run root 是其直接子目錄。 | 兩邊遵守相同 canonical runs 路徑與 32 位 hex 子目錄契約。 |
| wrapper 與身份 | `WorkerTools(mode="worker")` 使用安裝的 `local-worker-tool`，工具呼叫以 `sudo -n -u _cairnwork_worker` 執行；canary 另確認 worker identity。 | `local_worker.cli run` 建立相同的 worker-mode `WorkerTools`，用相同安裝 wrapper 與 sudo argv。 | 呼叫路徑相同；非 M4 環境不能證明實際 sudo 帳號及其檔案存取結果。 |
| run root 目錄 | root `0750`；`checkout/`、`tmp/`、`q/` 為 `0770`；canary 還建立 `venv/` 並安裝 pytest。 | 原 runtime 只建立 checkout 與 episodes，缺少 wrapper 強制要求的 `tmp/`、`q/`，也沒有 pytest venv。 | **確定根因**：第一次 `status`/`tracked` wrapper 呼叫在模型 turn 前拒絕缺少 `tmp`/`q`；補建兩目錄與 canary 同款 venv。 |
| checkout 權限 | fixture 目錄 `0770`、檔案 `0660`，工具以 worker group 測讀寫。 | PAUSE `_prepare` clone 後保留來源 mode；原 runtime 未調整 worker group 可寫模式。 | 依 canary 方式以不跟隨 symlink 的 descriptor walk 設目錄 `0770`、一般檔案 `0660`；實際 gid 仍須實機確認，debug log 記錄 uid/gid/mode。 |
| Python、cwd、env | canary 用 `--pyroot` 下的 CPython 3.13 建 venv；其工具子程序明確 `cwd="/"`、繼承呼叫端 env，worker wrapper/sudo 隨後套用自身環境邊界。 | CLI 用當前 `sys.executable` 啟動，明確 `cwd=task repo`；工具 wrapper 仍走相同 worker-mode 路徑。 | CLI cwd 是 Python module 載入所需；新建的 pytest venv 使用 canary 相同 CPython 3.13 選擇與 `uv venv`/`uv pip install pytest`。 |
| endpoint、model、context | Canary 只跑 isolation/probe 和 pytest，不送 agent chat 請求，也沒有 `--endpoint`、`--model` 或 context cap，因此沒有可重用的 endpoint 物件。 | Relay 使用 `http://127.0.0.1:18101/v1`、local `qwen3.5-9b`、首輪 32768／retry 65536；Haiku fallback 使用 proxy `/v1` URL、`claude-haiku-5-5`、65536；worker CLI 由 endpoint 字串建立自己的 `LoopbackEndpoint`。 | URL 形式符合 `LoopbackEndpoint` 的 `/v1` API；canary 無法證明服務端提供指定 model 或 agent prompt 可完成。 |
| task、prompt、tool/precheck | Canary task 是權限探針：合成檔案與 probe pytest；沒有修補任務 prompt。 | Relay 寫入 fixture/Issue prompt、允許路徑及公開測試，再用 `--write-path` 綁定 write scope；worker CLI 預設 tool schema/settings。`run_episode` 會呼叫 `AgentLoop.run`；該 run 先做 `status` 和 `tracked` 基線觀察，之後才開始 chat turn。`PrecheckMixin` 只在 agent 回報 `finish`/`DONE` 時執行。 | 原 run root 缺少 `tmp`/`q` 使前置基線工具拒絕，因此 agent loop 未開始且 token 為 0；這與 canary 通過並不矛盾，canary 沒有走這個 episode 啟動序列。 |

缺目錄造成前置拒絕是由 wrapper 的 `require_run_root`、AgentLoop 的基線觀察順序及舊 runtime 建置清單直接確認；本機沒有使用真 sudo、worker uid 或 `/Users/Shared` 重現。checkout gid 是否正確、以及安裝的 Python/pytest venv 是否能由 worker 執行，仍要在安裝環境確認；拒絕原因現在會留在 episode failure、ledger、CLI 與 0600 debug log。

## 行為與已知限制

- 啟用條件：`CAIRNWORK_LOCAL_RELAY=1`；`RelayConfig(enabled=True)` 單獨不能啟用。
- gate 丟例外或 fallback 成本無法確認時，該次失敗，不回報成功。
- 每次 worker 和 gate 回覆各自寫 ledger；fallback 僅在有有效 proxy stats 後記錄成本。ledger 寫入失敗會停止路由。
- acceptance 失敗時仍會呼叫 gate（約 US$0.0004 每次，layer2 11/19 裁定不改）。

## 跟實驗的差異

Fallback 與實驗 G1 H 臂同一路徑，差異只在是否經過 acceptance＋gate 的 relay 流程。
