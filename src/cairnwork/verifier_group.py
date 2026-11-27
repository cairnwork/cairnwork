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


Row = tuple[int, int, int, str]  # (pid, ppid, pgid, stat)


def _rows(user: str, timeout: float) -> list[Row] | None:
    """One ps listing of ``user``'s processes (None: cannot tell)."""
    flag = ["-x"] if sys.platform == "darwin" else []
    try:
        done = subprocess.run(  # noqa: S603 - fixed argv
            ["/bin/ps", *flag, "-U", user, "-o", "pid=,ppid=,pgid=,stat="],
            capture_output=True, text=True, timeout=timeout, check=False,
        )  # fmt: skip
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode not in (0, 1) or done.stderr.strip():
        return None
    rows: list[Row] = []
    for line in done.stdout.split("\n"):
        parts = line.split()
        if len(parts) == 4 and all(p.isdigit() for p in parts[:3]):
            rows.append((int(parts[0]), int(parts[1]), int(parts[2]), parts[3]))
    return rows


def _lead(rows: list[Row] | None, group: int) -> set[int]:
    """The collector's pid: the group leader itself, or (account mode, where the leader is sudo,
    which never shows in the account's ps) the group's process whose ppid is the group id. A
    process cannot forge its ppid, and once the collector exits its children go to launchd, so no
    leftover has it. Told by pid, never counted: a leftover must not take its place."""
    return {p for p, pp, g, _s in rows or () if g == group and (p == group or pp == group)}


def _members(rows: list[Row] | None, group: int) -> set[int]:
    """Pids in the collector's group other than the collector."""
    lead = _lead(rows, group)
    return {p for p, _, g, _s in rows or () if g == group and p not in lead}


def wait_clear(
    user: str, group: int, budget: float, on_first=None
) -> tuple[list[Row] | None, list[Row] | None]:
    """Wait until the group holds no process but the collector, the cap or ``budget`` seconds
    (what is left of reap's probe deadline) passed, or a lookup failed. Returns (first, last): the
    first listing, so the caller keeps what was outside the group then (a process that vanishes
    during the wait must still be counted), and the last one (None: it could not be read).
    ``on_first(rows)`` runs right after the first listing, before any wait: what the first look
    decides must not read a later state."""
    limit = min(CAP_SECONDS, budget)
    start = _now()
    first = last = None
    while True:
        left = limit - (_now() - start)
        if left <= 0:
            return first, last
        rows = _rows(user, min(left, PS_TIMEOUT_SECONDS))
        last = rows
        if first is None:
            first = rows
            if on_first is not None and rows:
                on_first(rows)
        if rows is None or not _members(rows, group):
            return first, last
        left = limit - (_now() - start)
        if left <= 0:
            return first, last
        _sleep(min(POLL_SECONDS, left))


def _live(rows: list[Row] | None, group: int) -> bool:
    """The collector is listed and is not a zombie: it still runs, so the rest of its group is
    its own in-flight children, which the group kill that follows reap() ends (not escapes)."""
    lead = _lead(rows, group)
    return any(p in lead and "Z" not in st for p, _, _, st in rows or ())


def count_escaped(account, group: int, deadline: float) -> int:
    """The account's escaped count (-1: cannot tell) from one listing per look, with the members
    of a live collector's group left out (the collector itself stays, ``main`` comes off it). The
    same listing decides both, so a child that comes and goes can never tip the count."""
    if group <= 0:  # no group known: the account's own count, as before #1849
        return account._escaped_count(deadline, attempts=1)

    def procs():
        left = deadline - time.monotonic()
        rows = _rows(account.user, min(left, PS_TIMEOUT_SECONDS)) if left > 0 else None
        if rows is None:
            return None
        skip, lead = _live(rows, group), _lead(rows, group)
        return [(p, pp) for p, pp, g, _s in rows if not (skip and g == group and p not in lead)]

    return verifier_escape.escaped_count(procs, account._real_paths, sys.platform == "darwin")


def _not_exempt(account, user: str, first: list[Row], keep) -> int:
    """Rows of the first listing that ``keep(pid, pgid)`` selects and are not an exempt daemon (-1:
    cannot tell). The exemption check reads the same rows, then re-lists as escaped_count does."""
    fresh = iter([first])

    def procs():
        rows = next(fresh, None) or _rows(user, PS_TIMEOUT_SECONDS)
        return None if rows is None else [(p, pp) for p, pp, g, _s in rows if keep(p, g)]

    return verifier_escape.escaped_count(procs, account._real_paths, sys.platform == "darwin")


def settle(account, group: int, main: int, deadline: float) -> int:
    """#1849: the wait for the group; returns the least count the run must keep, decided per pid
    at the FIRST listing: what was outside the group (not exempt, as main counts it, less
    ``main``), plus each non-exempt process of the group, other than the collector, still there
    when the wait ends. ``main`` comes off the first look's count only, never off a leftover:
    a count cannot tell the two apart. Unknown is one."""
    if group <= 0:
        return 0
    outside: list[int] = []
    lead: list[int] = []  # 1 when the collector itself is listed
    live: set[int] = set()

    def decide(rows: list[Row]) -> None:  # the exemption is judged at the first listing
        user = account.user
        outside.append(_not_exempt(account, user, rows, lambda p, g: g != group))
        heads = _lead(rows, group)
        lead.append(_not_exempt(account, user, rows, lambda p, g: p in heads))
        for pid in _members(rows, group):
            n = _not_exempt(account, user, rows, lambda p, g, pid=pid: p == pid)
            if n < 0:
                outside[0] = -1
            elif n:
                live.add(pid)

    first, last = wait_clear(account.user, group, max(deadline - time.monotonic(), 0.0), decide)
    if not first:  # no look, or an empty one (the collector is not even listed): fail closed
        return 1 if first is None else 0
    if outside[0] < 0 or lead[0] < 0:
        return 1
    base = max(outside[0] + max(lead[0], 0) - main, 0)  # main's own count, less ``main``
    if _live(last, group):  # a live collector: its group is its own, not a leftover
        return base
    return base + len(live if last is None else live & _members(last, group))
