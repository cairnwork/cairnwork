"""Supersede a FAILED task without letting two attempts overlap (#244 window 1).

A recovery that could not read the process table (SWEEP_UNAVAILABLE) leaves the dead attempt's
worker possibly running, and the task FAILED with a mark in the ledger (unswept.py). Superseding
the task and submitting its card again would start a second attempt beside that worker. So the
takeover re-runs the very sweep recovery uses (``confined.sweep_marked`` on the attempt's
``CAIRNWORK_ATTEMPT`` tag) and goes ahead only when the sweep now runs and, after killing what it
found, finds nothing left. Otherwise it refuses (``TakeoverRefused``, exit 2 of route) and records
the reason in the ledger, shown by ``status``. There is no override: fail closed, run it again.
A task whose attempt was checked (no mark) supersedes exactly as before.
"""

from __future__ import annotations

import time

from cairnwork.confined import ATTEMPT_ENV, SWEEP_UNAVAILABLE, sweep_marked
from cairnwork.ownership import OwnershipLedger, OwnershipRecord, TakeoverRefused

_CONFIRM_TRIES = 10  # re-sweeps after a kill, until the killed processes are gone
_CONFIRM_WAIT = 0.1


def supersede(ledger: OwnershipLedger, task_id: str) -> OwnershipRecord:
    """Mark the task SUPERSEDED. Raises TakeoverRefused while its attempt's processes are
    unchecked or still alive; InvalidStatusTransition as ``ledger.set_status`` does."""
    held = ledger.unswept_attempt(task_id)
    if held is not None:
        _recheck(ledger, task_id, held[0])
    return ledger.set_status(task_id, "SUPERSEDED")


def _recheck(ledger: OwnershipLedger, task_id: str, attempt_id: str) -> None:
    found = sweep_marked(ATTEMPT_ENV, attempt_id)
    tries = 0
    while found not in (0, SWEEP_UNAVAILABLE) and tries < _CONFIRM_TRIES:
        time.sleep(_CONFIRM_WAIT)  # it was SIGKILLed: wait until it is gone, then look again
        found, tries = sweep_marked(ATTEMPT_ENV, attempt_id), tries + 1
    if found == 0:
        ledger.clear_unswept(task_id)
        return
    why = (
        "SWEEP_UNAVAILABLE: the process table cannot be read; the attempt's processes are unchecked"
        if found == SWEEP_UNAVAILABLE
        else f"SURVIVORS_REMAIN: {found} process(es) of the attempt are still running"
    )
    ledger.note_refusal(task_id, f"{why} (attempt {attempt_id})")
    raise TakeoverRefused(f"{task_id}: {why}; the card stays held, retry after they are gone")
