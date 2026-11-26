"""#1819: a short wait before reap() refuses, so the collector's own exit is not a survivor.

reap() counts the account's processes. Right after a run the harness's own collector (the
``main`` the caller ends on purpose) may still be exiting, and a one-line child it started may
still be going; counted at that instant they refused a harmless change. ``settle`` re-counts for
at most GRACE_SECONDS, and only while something beyond ``main`` is there. It never trusts an
answer it cannot read: an unknown count (-1) is refused at once, and whatever is still there when
the cap is spent stays counted. ``main`` is only taken off while that run is still alive.
"""

from __future__ import annotations

import time
from collections.abc import Callable

GRACE_SECONDS = 2.0
GRACE_INTERVAL = 0.25


class Main(int):
    """The caller's own run: ``int`` (how many, as before) plus whether it is still alive."""

    alive: Callable[[], bool] | None

    def __new__(cls, count: int, alive: Callable[[], bool] | None = None) -> Main:
        obj = super().__new__(cls, count)
        obj.alive = alive
        return obj


def bounded(deadline: float | None, cap: float) -> float:
    """``cap`` seconds, or what is left until ``deadline`` if that is less (never below 0.1)."""
    return cap if deadline is None else max(min(cap, deadline - time.monotonic()), 0.1)


def _is_alive(alive: Callable[[], bool] | None) -> bool:
    """True unless the run is known gone; a probe that cannot answer counts as gone, so the
    process is refused rather than taken off (never a crash out of reap())."""
    if alive is None:
        return True
    try:
        return bool(alive())
    except Exception:  # noqa: BLE001 - any failure to ask means we cannot vouch for main
        return False


def settle(account, before: int, main: int, deadline: float):
    """(count, main, deadline): the account's count re-read until nothing beyond ``main`` is
    left, at most GRACE_SECONDS in all (a slow recount is cut at the cap too); ``main`` is 0 once
    its run is gone; the deadline moves by the time spent, never more than the cap."""
    start = time.monotonic()
    alive = getattr(main, "alive", None)
    taken = int(main)
    while True:
        if taken and not _is_alive(alive):
            taken = 0  # nothing of ours is left to take off: anything counted is a survivor
        if before - taken <= 0 or time.monotonic() - start >= GRACE_SECONDS:
            break  # nothing beyond main (or unknown: -1), or the cap is spent
        time.sleep(GRACE_INTERVAL)
        cap = min(deadline, start + GRACE_SECONDS)
        before = account._escaped_count(cap, attempts=1)  # noqa: SLF001 - same package
    return before, taken, deadline + min(time.monotonic() - start, GRACE_SECONDS)
