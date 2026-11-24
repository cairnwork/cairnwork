"""A bounded read of git output for the verifier's reads of candidate-controlled objects (#1515).

``run_git_bytes`` without ``max_bytes`` collects all of git's stdout in memory, so a candidate
commit holding one huge blob (or tree) could exhaust the verifier's memory. ``read_git_bytes``
holds the read to ``MAX_GIT_READ`` (the bound ``tamper_scan`` already uses, #766): past it git is
killed and the read fails as a ``GitCommandError``, which every caller already treats as an
unreadable change (refused, never accepted).
"""

from __future__ import annotations

from pathlib import Path

from cairnwork.gitcmd import GitCommandError, run_git_bytes
from cairnwork.gitcmd_capped import GitOutputTooLarge
from cairnwork.tamper_scan_budget import MAX_TOKENIZED

MAX_GIT_READ = MAX_TOKENIZED  # bytes read from git per call; the same bound as tamper_scan's


def read_git_bytes(repo: Path, *args: str, stdin: bytes | None = None) -> bytes:
    """``run_git_bytes`` with stdout held to ``MAX_GIT_READ``; over it: GitCommandError."""
    limit = MAX_GIT_READ
    try:
        return run_git_bytes(repo, *args, stdin=stdin, max_bytes=limit)
    except GitOutputTooLarge as exc:
        raise GitCommandError(f"git output is over {limit} bytes") from exc
