"""Wait for a child to exit without reaping it (Issue #1549, moved out of confined.py).

The leader stays a zombie, so its process group id stays reserved until the group has been
signalled (#1163). ``os.waitid`` does this on Linux and, from Python 3.13, on macOS; macOS with
3.11 or 3.12 has none, so it asks the kernel for the exit with a kqueue instead (NOTE_EXIT is
reported for a zombie too and never reaps). A host with neither fails loudly, never as a verdict.
"""

from __future__ import annotations

import os
import select
import time

_POLL_SECONDS = 0.005


def wait_exited_unreaped(pid: int, timeout: float | None) -> bool:
    """True if child ``pid`` exited within ``timeout`` seconds (None: no limit), still unreaped."""
    if hasattr(os, "waitid"):
        return _poll_waitid(pid, timeout)
    if hasattr(select, "kqueue"):
        return _wait_kqueue(pid, timeout)
    raise NotImplementedError("no os.waitid and no kqueue: cannot wait for a child unreaped")


def _poll_waitid(pid: int, timeout: float | None) -> bool:
    deadline = None if timeout is None else time.monotonic() + timeout
    while True:
        if os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None:
            return True
        if deadline is not None and time.monotonic() >= deadline:
            return False
        time.sleep(_POLL_SECONDS)


def _wait_kqueue(pid: int, timeout: float | None) -> bool:
    queue = select.kqueue()
    try:
        change = select.kevent(
            pid,
            select.KQ_FILTER_PROC,
            select.KQ_EV_ADD | select.KQ_EV_ONESHOT,
            select.KQ_NOTE_EXIT,
        )
        try:
            return bool(queue.control([change], 1, timeout))
        except ProcessLookupError:  # already gone and reaped: it did exit
            return True
    finally:
        queue.close()
