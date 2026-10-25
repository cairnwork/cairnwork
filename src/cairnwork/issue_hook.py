"""The post-verified hook step of the Issue runner's settle (post_verified.py), owed across a crash.

settle opens the PR of a Core-verified task, labels the Issue done, comments, and only then runs
the hook, so the hook can never change the outcome (docs/ATTESTATION.md). A crash after the label
and before the hook used to lose it silently: the Issue is no longer ``cairnwork:running``, so no
later pass looked at it again (#63 (d)). So settle writes an owed marker
(``<state>/issues/<repo>__<n>__<task id>.hook-owed.json``: one per task, so an Issue run again
keeps what an earlier task still owes) before it moves the label, and drops it once the hook
step has run. Each pass then runs the hook of every owed task whose Issue it did
not find running (those still running are settled, and their hook run, by the usual path), so a
hook still runs only after the labels are final. At least once: a crash after the hook and before
the marker is dropped runs it again on the next pass, recorded again in the hook's log (the
intended hook, ``cairnwork-attest on-verified``, then reuses the bundle it wrote). Nothing here
changes the settle result or the exit code; trouble is alerted and the marker kept, every pass
until the step runs (a maintainer who gives the hook up removes the file).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from cairnwork.issue_publish import TASK_ID
from cairnwork.post_verified import alert, run_post_verified_hook
from cairnwork.workspace import ATTEMPT_ID

OWED = ".hook-owed.json"
COMMIT = re.compile(r"[0-9a-f]{40}")


def _owed_path(state: Path, repo: str, number: int, task_id: str) -> Path:
    return state / "issues" / f"{repo.replace('/', '__')}__{number}__{task_id}{OWED}"


def owe(intake, repo: str, number: int, task_id: str) -> None:
    """Durable before the done label (replace: a crash leaves the old file or the new one)."""
    path = _owed_path(intake.state, repo, number, task_id)
    path.parent.mkdir(mode=0o700, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps({"repo": repo, "issue": number, "task_id": task_id}))
    os.replace(tmp, path)


def run(intake, github, clone: Path, number: int, status: dict[str, Any]) -> None:
    """The hook step, then the marker goes, but only once the step has run: a step skipped by an
    error (an unreadable config that names a hook, an exception before the hook's own record)
    keeps it, so a later pass runs it again."""
    if post_verified(intake, github, clone, status):
        _owed_path(intake.state, github.repo, number, str(status["task_id"])).unlink(
            missing_ok=True
        )


def run_owed(intake, github, clone: Path, running: set[int]) -> None:
    """The owed hooks of this repo's Issues that are not running: their labels are final."""
    prefix = f"{github.repo.replace('/', '__')}__"
    for path in sorted((intake.state / "issues").glob(f"{prefix}*{OWED}")):
        try:
            owed = json.loads(path.read_text())
            number = int(owed["issue"])
            if owed["repo"] != github.repo or number in running:
                continue
            status = intake.status(task_id=str(owed["task_id"]))
            if not _verified(status, str(owed["task_id"])):
                raise ValueError(f"task {owed['task_id']} is not a Core-verified candidate")
            run(intake, github, clone, number, status)
        except Exception as exc:  # noqa: BLE001 - never a settle result; kept for the next pass
            alert(f"owed hook {path.name}: {type(exc).__name__}: {exc}")


def _verified(status: dict[str, Any], task_id: str) -> bool:
    """What _open_pr checked before the marker was written, read again from the state."""
    return (
        status.get("status") == "COMPLETED"
        and bool(status.get("core_verified"))
        and str(status.get("task_id")) == task_id
        and TASK_ID.fullmatch(task_id) is not None
        and ATTEMPT_ID.fullmatch(str(status.get("attempt_id"))) is not None
        and COMMIT.fullmatch(str(status.get("candidate_commit"))) is not None
    )


def post_verified(intake, github, clone: Path, status) -> bool:
    """The opt-in hook, after the outcome is final: logged and alerted, never a verdict. True when
    the step is done: no hook configured, or the hook ran (whatever it returned, which its log
    records); False when an error skipped it."""
    try:
        config = intake.config()
    except Exception as exc:  # noqa: BLE001 - off means silent, so alert only if it names a hook
        if _names_hook(intake.state / "config.json"):
            alert(f"skipped: {type(exc).__name__}")
            return False
        return True
    try:  # status was validated by _open_pr (or _verified)
        run_post_verified_hook(
            intake.state, config, task_id=str(status["task_id"]),
            attempt_id=str(status["attempt_id"]), candidate=str(status["candidate_commit"]),
            clone=clone, repo_slug=github.repo, gh=github.gh,
        )  # fmt: skip
    except Exception as exc:  # noqa: BLE001 - nothing here may change the settle result
        alert(f"skipped: {type(exc).__name__}")
        return False
    return True


def _names_hook(path: Path) -> bool:
    """Whether the (now unreadable) config sets a hook path; an explicit null does not count."""
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return False
    return re.search(r'"post_verified_hook"\s*:\s*"', text) is not None
