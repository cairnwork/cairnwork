"""What cairnwork-issues may say on GitHub (PRESERVED #24).

Comments carry only allow-listed words: a fixed status, a phase from the journal's fixed list
and an error *code*. Never ``detail`` (a host path after a crash), exception text, worker output
or Issue text. Those stay in the local JSON output."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from cairnwork.journal import PHASES
from cairnwork.provenance import clean

STATUSES = frozenset({"FAILED", "CANCELLED", "SUPERSEDED", "COMPLETED"})
BOT_GH = "nexus-agent-gh"
PROBE = "/installation/repositories"
PROBE_TIMEOUT = 60
REFUSAL_CODE = re.compile(r"[A-Z0-9_]{1,64}")
TASK_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")  # plain text: no markdown, @, #, spaces
PR_URL = r"https://github\.com/{repo}/pull/[0-9]+"


def pr_url(url: str, repo: str) -> str:
    """The PR URL gh printed, if it really is a PR of the configured repo; else empty."""
    return url if re.fullmatch(PR_URL.format(repo=re.escape(repo)), url) else ""


def done_text(url: str, repo: str) -> str:
    safe = pr_url(url, repo)
    tail = f"draft PR：{safe}" if safe else "draft PR 已開，連結在 Cairnwork 本機輸出。"
    return f"Cairnwork 修好並通過 Core 驗證，{tail}"


def failure_text(status: dict) -> str:
    """``uncomparable`` (#234) is the code the owner journaled at FINISHED (journal.no_evidence);
    without it, as for rows from before #234, the text never claims there was no evidence."""
    word = status["status"] if status["status"] in STATUSES else "UNKNOWN"
    phase = status.get("phase")
    where = f"（最後階段 `{phase}`）" if phase in PHASES else ""
    code = status.get("uncomparable")
    if word == "FAILED" and isinstance(code, str) and REFUSAL_CODE.fullmatch(code):
        where += (
            f"。不可比較／沒有證據（`{code}`）：base、它的環境或驗證帳號無法判斷，"
            "這次沒有產生證據，不是對候選修正的判定"
        )
    return f"Cairnwork 沒有完成：`{word}`{where}。細節在 Cairnwork 本機輸出，不貼在這裡。"


def refusal_code(exc: Exception) -> str:
    code = getattr(exc, "code", "")
    return code if isinstance(code, str) and REFUSAL_CODE.fullmatch(code) else "REFUSED"


def require_bot_gh(gh: str, repo: str, cwd: Path) -> None:
    """Fail closed unless --gh is the bot wrapper AND it really acts as the GitHub App.

    The name check only catches a misconfiguration. The probe is the identity check:
    ``GET /installation/repositories`` answers only to an installation token (a personal login
    gets 403) and must list the configured repo. Any error, timeout or odd output refuses."""
    if Path(gh).name != BOT_GH:
        raise RuntimeError(f"GH_IDENTITY_NOT_BOT: --gh must be the {BOT_GH} wrapper")
    try:
        done = subprocess.run(  # noqa: S603 - fixed binary, argv list, no shell
            [gh, "api", PROBE, "--paginate", "--jq", ".repositories[].full_name"],
            capture_output=True, text=True, timeout=PROBE_TIMEOUT, check=False, cwd=cwd,
        )  # fmt: skip
    except (OSError, subprocess.SubprocessError):
        done = None
    if done is None or done.returncode != 0 or repo.lower() not in done.stdout.lower().split():
        raise RuntimeError(f"GH_IDENTITY_NOT_BOT: {BOT_GH} has no installation token for {repo}")


def pr_body(number: int, task_id: str, attempt: str, commit: str, provenance: dict) -> str:
    """Only validated fields: the caller has matched task id, attempt and commit, and
    ``provenance`` comes from the commit's own trailers (provenance.parse)."""
    who = provenance_lines(provenance)
    return (
        f"Refs #{number}\n\nCairnwork: 受影響（Cairnwork 試點：Issue 直接送 Cairnwork 修）\n\n"
        f"- Cairnwork task `{task_id}`，attempt `{attempt}`\n"
        f"- 候選 commit `{commit}`，Core 判定 VERIFIED（Cairnwork notary）\n"
        "- 這是 draft：照一般規則審查、檢查全綠後再由審查者合併；Cairnwork 不合併。\n\n"
        f"<!-- NEXUS_CORE_ISSUE: {number} -->\n<!-- NEXUS_ISSUE_INTENT_V1\n"
        f'[{{"issue": {number}, "on_merge": "KEEP_OPEN"}}]\nNEXUS_ISSUE_INTENT_V1 -->\n\n'
        f"{who}\n"
    )


def provenance_lines(provenance: dict) -> str:
    """Worker, requested model, observed model; each cleaned again, so a bad value is unknown."""
    return (
        f"Worker: {clean(provenance.get('worker'))}\n"
        f"Requested model: {clean(provenance.get('requested_model'))}\n"
        f"Observed model: {clean(provenance.get('observed_model'))}"
    )


def refusal_text(exc: Exception, needs_fix: str, queue: str) -> str:
    if refusal_code(exc) == "TASK_BOUND_REQUEST_UNREADABLE":  # the block is not at fault (#466)
        return (
            "Cairnwork 不能接這張：`TASK_BOUND_REQUEST_UNREADABLE`。區塊沒有寫錯："
            "它綁定的任務已失敗，存下的請求讀不到。只改區塊再排，請求內容沒變時"
            "會被 `REPLAY_FORBIDDEN` 擋下。請維護者先用 `cairnwork-route supersede` "
            "釋放那個任務（task id 在 run-once 的本機輸出），再修改區塊、"
            f"移除 `{needs_fix}` 再加上 `{queue}`。"
        )
    return (
        f"Cairnwork 不能接這張：`{refusal_code(exc)}`。修正 `eia-task` 區塊，移除 "
        f"`{needs_fix}` 再加上 `{queue}`。測試檔不能列在 allowed_files，要先放在 main。"
    )
