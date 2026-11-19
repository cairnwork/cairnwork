# experimental/

實驗能力放這裡，預設關閉。每個子資料夾要有 `OWNER`（負責 session）和 `EXPIRES`（到期日，ISO 日期）。核心 `src/cairnwork/` 不得 import 這裡的任何東西（import-linter 強制）。到期未證明有效就刪除。

負責人明令保留的能力列在 `tests/test_experimental_expiry_1249.py` 的 `OWNER_RETAINED`（`EXPIRES` 寫 `retained`），不受 14 天限制，仍預設關閉。

- `local_relay/`：Issue #1513，本機優先 relay（OWNER `m4-cw-relay`，到期 2026-12-03）。
