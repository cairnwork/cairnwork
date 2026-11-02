"""The per-attempt trust re-check for an Issue task (PRESERVED #2): before every attempt the
Issue's task block and its queueing must still be what was trusted at dispatch."""

from __future__ import annotations

import hashlib
from collections.abc import Callable

from cairnwork.issue_runner import QUEUE, TRUST_CHANGED, GitHub
from cairnwork.issue_task import TRUST_CHECK_FAILED, task_block_text
from cairnwork.journal import TRUST_UNREAD


def attempt_gate(github: GitHub, number: int, block_sha256: str) -> Callable[[], str]:
    """The refusal code (or "") for the Issue; anything unreadable is a refusal. Trust is asked
    first, as take and settle_check do: an untrusted body is never read. A GitHub read that failed
    (TRUST_CHECK_FAILED, a gh error or timeout) is TRUST_UNREAD, no evidence either way, not a
    trust change (#673)."""

    def gate() -> str:
        try:
            reason = github.trust(number, QUEUE)
            if reason:
                return TRUST_UNREAD if reason == TRUST_CHECK_FAILED else TRUST_CHANGED
            body = github.current(number).get("body") or ""
        except Exception:  # noqa: BLE001 - fail closed: refused, with no evidence either way
            return TRUST_UNREAD
        try:  # the block text take bound, not a re-parse: test_roots may change meanwhile (#661)
            block = hashlib.sha256(task_block_text(body).encode()).hexdigest()
        except Exception:  # noqa: BLE001 - fail closed: an unreadable block is a changed Issue
            return TRUST_CHANGED
        return "" if block == block_sha256 else TRUST_CHANGED

    return gate
