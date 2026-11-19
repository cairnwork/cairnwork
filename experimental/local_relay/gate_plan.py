"""What the Haiku gate reviews: the task statement plus the change itself, with relative paths."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from experimental.pause.git_env import hardened_git_env

MAX_PLAN_CHARS = 30000
MAX_NEW_FILE_CHARS = 8000
_GIT = (
    "git",
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "core.fsmonitor=false",
    "-c",
    "core.quotepath=off",
)


def _git(checkout: Path, *args: str) -> bytes:
    run = subprocess.run(
        [*_GIT, "-C", str(checkout), *args],
        capture_output=True,
        check=False,
        env=hardened_git_env(),
    )
    if run.returncode:
        raise RuntimeError("cannot read the candidate change for the gate")
    return run.stdout


def build_gate_plan(task: dict[str, Any], candidate: Any) -> str:
    """Task prompt + ``git diff base`` + new untracked files; paths are repo-relative.

    Never the candidate's repr: that carries host paths (the secret scanner rightly blocks them)
    and says nothing about what actually changed.
    """
    checkout = getattr(candidate, "checkout", None)
    base = getattr(candidate, "base_sha", None)
    if checkout is None or not isinstance(base, str):
        raise ValueError("gate review needs the candidate checkout and its base commit")
    checkout = Path(checkout)
    diff = _git(checkout, "diff", "--no-color", "--no-ext-diff", "--no-renames", base).decode(
        "utf-8", "replace"
    )
    parts = [
        "Task:\n" + str(task.get("prompt", "")).strip(),
        "Allowed files: " + ", ".join(task.get("allowed_files", [])),
        "Change (git diff against the base commit):\n" + (diff or "(no tracked changes)"),
    ]
    for raw in _git(checkout, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0"):
        if not raw:
            continue
        rel = raw.decode("utf-8", "replace")
        text = (checkout / rel).read_bytes()[:MAX_NEW_FILE_CHARS].decode("utf-8", "replace")
        parts.append(f"New file {rel}:\n{text}")
    plan = "\n\n".join(parts)
    if len(plan) > MAX_PLAN_CHARS:
        plan = plan[:MAX_PLAN_CHARS] + "\n[truncated]"
    return plan + "\n\nReply on the first line with exactly ACCEPT or REJECT, then a short reason."
