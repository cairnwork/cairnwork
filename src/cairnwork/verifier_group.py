"""#1849: let the collector's own exiting children go before reap() counts the account.

The collector (``main``) runs in its own process group. When it exits, its short-lived children
(a resource tracker, for one) can still be present for a moment, and reap() counted them as
escapes. Before the count, wait, capped, until the group holds no process but the collector
itself. This never recounts and never changes a count: it only delays the one count. A process
outside the group, or one still there at the cap, is counted as before. The lookup is the
account's own ps, one bounded call per poll; unreadable means no wait (fail closed).
"""

from __future__ import annotations

import subprocess
import sys
import time

CAP_SECONDS = 2.0
POLL_SECONDS = 0.1
PS_TIMEOUT_SECONDS = 5.0
_now = time.monotonic  # hooks for the tests' fake clock
_sleep = time.sleep


def _members(user: str, group: int, timeout: float) -> int | None:
    """How many processes of ``user`` are in process group ``group`` (None: cannot tell)."""
    flag = ["-x"] if sys.platform == "darwin" else []
    try:
        done = subprocess.run(  # noqa: S603 - fixed argv
            ["/bin/ps", *flag, "-U", user, "-o", "pid=,pgid="],
            capture_output=True, text=True, timeout=timeout, check=False,
        )  # fmt: skip
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode not in (0, 1) or done.stderr.strip():
        return None
    count = 0
    for line in done.stdout.split("\n"):
        parts = line.split()
        if len(parts) == 2 and all(p.isdigit() for p in parts):
            count += int(parts[1]) == group
    return count


def wait_clear(user: str, group: int, main: int, deadline: float) -> None:
    """Return when the group has at most ``main`` members (the collector, if still running), the
    cap or ``deadline`` (a time.monotonic value shared with reap) passed, or a lookup failed."""
    if group <= 0:  # no group given: no wait, behaviour as before
        return
    budget = min(CAP_SECONDS, deadline - time.monotonic())
    start = _now()
    while True:
        left = budget - (_now() - start)
        if left <= 0:
            return
        found = _members(user, group, min(left, PS_TIMEOUT_SECONDS))
        if found is None or found <= main:
            return
        left = budget - (_now() - start)
        if left <= 0:
            return
        _sleep(min(POLL_SECONDS, left))
