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


def settle(account, before: int, main: int, deadline: float):
    """(count, main, deadline): the account's count re-read until nothing beyond ``main`` is
    left, at most GRACE_SECONDS; ``main`` is 0 once its run is gone; the deadline moves by the
    time spent."""
    start = time.monotonic()
    alive = getattr(main, "alive", None)
    taken = int(main)
    while True:
        if alive is not None and not alive():
            taken = 0  # nothing of ours is left to take off: anything counted is a survivor
        if before - taken <= 0 or time.monotonic() - start >= GRACE_SECONDS:
            break  # nothing beyond main (or unknown: -1), or the cap is spent
        time.sleep(GRACE_INTERVAL)
        before = account._escaped_count(deadline, attempts=1)  # noqa: SLF001 - same package
    return before, taken, deadline + (time.monotonic() - start)
