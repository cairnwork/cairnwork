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

from cairnwork import verifier_escape

CAP_SECONDS = 2.0
POLL_SECONDS = 0.1
PS_TIMEOUT_SECONDS = 5.0
_now = time.monotonic  # hooks for the tests' fake clock
_sleep = time.sleep


Row = tuple[int, int, int]  # (pid, ppid, pgid)


def _rows(user: str, timeout: float) -> list[Row] | None:
    """One ps listing of ``user``'s processes (None: cannot tell)."""
    flag = ["-x"] if sys.platform == "darwin" else []
    try:
        done = subprocess.run(  # noqa: S603 - fixed argv
            ["/bin/ps", *flag, "-U", user, "-o", "pid=,ppid=,pgid="],
            capture_output=True, text=True, timeout=timeout, check=False,
        )  # fmt: skip
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode not in (0, 1) or done.stderr.strip():
        return None
    rows: list[Row] = []
    for line in done.stdout.split("\n"):
        parts = line.split()
        if len(parts) == 3 and all(p.isdigit() for p in parts):
            rows.append((int(parts[0]), int(parts[1]), int(parts[2])))
    return rows


def wait_clear(user: str, group: int, main: int, budget: float) -> list[Row] | None:
    """Wait until the group has at most ``main`` members (the collector, if still running), the
    cap or ``budget`` seconds (what is left of reap's probe deadline) passed, or a lookup failed.
    Returns the first listing (None: no usable look), so the caller can keep what was outside the
    group then: a process outside it that vanishes during the wait must still be counted, or the
    wait would let a run pass that main refuses."""
    limit = min(CAP_SECONDS, budget)
    start = _now()
    first = None
    while True:
        left = limit - (_now() - start)
        if left <= 0:
            return first
        rows = _rows(user, min(left, PS_TIMEOUT_SECONDS))
        if first is None:
            first = rows
        if rows is None or sum(r[2] == group for r in rows) <= main:
            return first
        left = limit - (_now() - start)
        if left <= 0:
            return first
        _sleep(min(POLL_SECONDS, left))


def _not_exempt(account, user: str, first: list[Row], keep) -> int:
    """Rows of the first listing that ``keep`` selects and are not an exempt daemon (-1: cannot
    tell). The exemption check reads the same rows, then re-lists as escaped_count always does."""
    fresh = iter([first])

    def procs():
        rows = next(fresh, None) or _rows(user, PS_TIMEOUT_SECONDS)
        return None if rows is None else [(p, pp) for p, pp, g in rows if keep(g)]

    return verifier_escape.escaped_count(procs, account._real_paths, sys.platform == "darwin")


def settle(account, group: int, main: int, deadline: float) -> int:
    """#1849: the wait for the group; returns the least count the run must keep: the processes
    that were outside the group at the first look, as main counts them (the total not exempt
    minus ``main`` or the group's own, whichever is more), from ONE listing. Unknown is one."""
    if group <= 0:
        return 0
    first = wait_clear(account.user, group, main, max(deadline - time.monotonic(), 0.0))
    if not first:  # no look, or an empty one (the collector is not even listed): fail closed
        return 1 if first is None else 0
    outside = _not_exempt(account, account.user, first, lambda g: g != group)
    held = _not_exempt(account, account.user, first, lambda g: g == group)
    return 1 if outside < 0 or held < 0 else max(outside + held - max(held, main), 0)
