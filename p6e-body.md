Closes #1450
<!-- NEXUS_CORE_ISSUE: 1450 -->
<!-- NEXUS_ISSUE_INTENT_V1
[{"issue": 1450, "on_merge": "CLOSE"}]
NEXUS_ISSUE_INTENT_V1 -->

已核對（main）：sealed gate（`tests/pra1353_gate.py`）只檢查 base install 不在被檢查的樹內，且跑在它所檢查的直譯器裡，無法證明該直譯器沒被動過。

做法（layer1 11/19 指示：受信步驟記錄 base Python 路徑與 stdlib／lib-dynload 雜湊；已在 #1464 之上做）：
- 新 `tests/pra1353_base_record.py`（只用標準庫，`python -I -S -B`）：`record OUT` 寫下 base_prefix／base_exec_prefix、stdlib／platstdlib／lib-dynload 路徑與整棵樹的 SHA-256（相對路徑、型別、權限位元、內容；symlink 記目標；非一般檔／symlink 一律報錯；略過 `__pycache__`、`site-packages`）；`verify OUT` 重算並比對，記錄缺、格式不對、樹讀不到、任何差異都失敗（fail closed）。路徑取自 sysconfig 的 base 變數，所以 venv 內執行與 base 直譯器算出同一組根。
- `ci.yml`（`test`、`test-macos` 各一次）：在 #1454 B2 的受信 venv 步驟內、`uv sync` 之後，用該 venv 的直譯器 record 到 `$RUNNER_TEMP`（樹外），此時還沒有 PR 程式碼執行（只有 recorder 本身）；在 sealed gate 步驟內、gate 之前 verify，失敗就不跑 gate 並以非 0 結束。沒有新增獨立步驟，所以 `test_1454_b2_lock_trust.py` 對 gate 前步驟的限制（只有一個建置步驟、無其他 `uv`／`scripts/`）仍成立。
- 範圍：只涵蓋 record 之後到 gate 之前 base install 被改動的情況；recorder 與 verifier 檔本身來自 PR 檢出，其完整性由合併前審查負責，不在本 PR 宣稱。

測試：`tests/test_pra1353_base_record_1450.py` 14 項（任何改動／新增／刪除／symlink 改指／權限／改名都使雜湊改變；FIFO fail closed；record 不覆寫；缺記錄或竄改記錄 rc 1；venv 與 base 一致；`ci.yml` 兩個 job 的 record／verify 步驟順序）；`test_1454_b2_lock_trust.py` 等共 36 passed；pin／1353 相關另有 143 passed；ruff、pyright（0 錯）、lint-imports、`check_size.py` rc=0。gate 步驟的 shell 判斷已在本機模擬（verify 失敗 → 跳過 gate、rc=1；通過 → 照常跑 gate）。

風險：實際行為以 CI 為準（本機無法跑 GitHub 上的 uv 環境）；record 與 gate 之間只有受信 venv 建置，沒有 PR 程式碼。pin guard 已不再是必要檢查。

HIGH 路徑（.github/workflows），需要跨家審查。

Cairnwork: 受影響

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_01Hyu7uW15o467Cdrp2yU8DH
