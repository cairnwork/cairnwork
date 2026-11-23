"""Wait for a child to exit without reaping it (Issue #1549, moved out of confined.py).

The leader stays a zombie, so its process group id stays reserved until the group has been
signalled (#1163). ``os.waitid`` does this on Linux and, from Python 3.13, on macOS; macOS with
3.11 or 3.12 has none, so it asks the kernel through a kqueue instead, which never reaps. A host
with neither fails loudly, never as a verdict.
"""

from __future__ import annotations

import errno
import os
import select
import time
from typing import Any

_POLL_SECONDS = 0.005

# kqueue exists on BSD and macOS only: typed as Any so the Linux type check reads the same code
_select: Any = select


def wait_exited_unreaped(pid: int, timeout: float | None) -> bool:
    """True if child ``pid`` exited within ``timeout`` seconds (None: no limit), still unreaped.

    Precondition: ``pid`` is a child of the caller that the caller has not reaped. ``os.waitid``
    raises ChildProcessError for any other pid; the kqueue form cannot tell a foreign live pid
    from a child, so for one it simply waits for its exit (behaviour recorded in the tests, not a
    promise). The only caller, confined, passes its own just-started leader."""
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
    """macOS reports a child that already exited (a zombie) as an ESRCH error event, not as
    NOTE_EXIT, so that is told apart from a pid that is gone or was never ours: only a pid that
    is still there (``os.kill(pid, 0)``) counts as exited. Any other error raises, as waitid
    does. That check only means "some process holds this pid": it is the exited child only because
    the caller's precondition (see wait_exited_unreaped) says a zombie child keeps its pid."""
    queue = _select.kqueue()
    try:
        change = _select.kevent(
            pid,
            _select.KQ_FILTER_PROC,
            _select.KQ_EV_ADD | _select.KQ_EV_ONESHOT,
            _select.KQ_NOTE_EXIT,
        )
        events = queue.control([change], 1, None if timeout is None else max(0.0, timeout))
    finally:
        queue.close()
    for event in events:
        if event.flags & _select.KQ_EV_ERROR:
            _exited_zombie_or_raise(pid, event.data)
            return True
        if event.fflags & _select.KQ_NOTE_EXIT:
            return True
    return False


def _exited_zombie_or_raise(pid: int, error: int) -> None:
    if error != errno.ESRCH:
        raise OSError(error, os.strerror(error))
    try:
        os.kill(pid, 0)  # a zombie can still be signalled; a reaped or foreign pid cannot
    except ProcessLookupError:
        raise ChildProcessError(errno.ECHILD, os.strerror(errno.ECHILD)) from None
