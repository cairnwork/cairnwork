"""Supersede a FAILED task without letting two attempts overlap (#244 window 1).

A FAILED task whose attempt's processes are not shown gone carries a mark in the ledger
(unswept.py); superseding it and submitting its card again would start a second attempt beside
them. So the takeover runs the sweep recovery uses (``confined.sweep_marked`` on the attempt's
``CAIRNWORK_ATTEMPT`` tag, and the worker account the attempt ran as) and looks again until the
processes are shown gone (``confined.confirm_gone``): the whole table read, no marked process
listed, none of the owner's processes hiding its environment, and the worker account idle (its
processes cannot be inspected, so any one of them keeps the fence). Only then does it supersede,
dropping the mark in the same ledger transaction. Otherwise it refuses (``TakeoverRefused``,
exit 2 of route) with the reason's code (``Sweep.problem``) and records the reason, shown by
``status``. There is no override: fail closed, run it again. A task without a mark supersedes
exactly as before.

A refusal names what keeps the fence (``proc_names.describe``: pids and sanitised command
names), so the operator can stop those processes or wait for them, then run the takeover again.

A LEGACY mark (made at the ledger migration; the attempt is unknown to the ledger) needs the
journal: every attempt it names for the task is swept, with ``account`` (the state's configured
worker account) as the account. No journal: refused. No journaled attempt proves that nothing was
started only if the journal already existed when the task was bound: its oldest row is older than
the task's bound request (``request``, written once right after the ledger bind; its mtime). A
missing or recreated journal is empty or starts later, so the takeover is refused
(LEGACY_ATTEMPT_UNKNOWN) and fails closed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import NoReturn

from cairnwork import unswept
from cairnwork.confined import ATTEMPT_ENV, Sweep, confirm_gone, sweep_marked
from cairnwork.journal import Journal
from cairnwork.ownership import OwnershipLedger, OwnershipRecord, TakeoverRefused
from cairnwork.proc_names import describe


def supersede(
    ledger: OwnershipLedger,
    task_id: str,
    *,
    journal: Journal | None = None,
    account: str | None = None,
    request: Path | None = None,
) -> OwnershipRecord:
    """Mark the task SUPERSEDED. Raises TakeoverRefused while its attempt's processes are not
    shown gone; InvalidStatusTransition as ``ledger.set_status`` does."""
    held = ledger.unswept_attempt(task_id)
    if held is None:
        return ledger.set_status(task_id, "SUPERSEDED")
    if held.attempt_id != unswept.LEGACY:
        attempts, account = [held.attempt_id], held.account or None
    else:
        attempts = _legacy_attempts(ledger, task_id, journal, request)
    for attempt_id in attempts:
        found = _swept(attempt_id, account)
        if not found.gone:
            kept = describe(found.pids) or (
                f"process table restricted ({found.restricted})" if found.restricted else ""
            )
            keeping = f"; kept by: {kept}" if kept else ""
            _refuse(ledger, task_id, f"{found.problem()} (attempt {attempt_id}){keeping}")
    return ledger.set_status(task_id, "SUPERSEDED", swept=held.attempt_id)


def _legacy_attempts(
    ledger: OwnershipLedger, task_id: str, journal: Journal | None, request: Path | None
) -> list[str]:
    if journal is None:
        _refuse(ledger, task_id, "LEGACY_ATTEMPT_UNKNOWN: the ledger does not know the attempt")
    attempts = sorted({e.attempt_id for e in journal.entries(task_id)})
    if attempts:
        return attempts
    first, bound = journal.first_at(), _bound_at(request)
    if first is None or bound is None or first >= bound:
        _refuse(
            ledger,
            task_id,
            "LEGACY_ATTEMPT_UNKNOWN: no journaled attempt, and the journal is not shown older"
            " than the task (missing, recreated, or the bound request is gone)",
        )
    return []  # the journal predates the task and names no attempt: nothing was started


def _bound_at(request: Path | None) -> datetime | None:
    try:
        return datetime.fromtimestamp(request.lstat().st_mtime, UTC) if request else None
    except OSError:
        return None  # unknown: fail closed


def _swept(attempt_id: str, account: str | None) -> Sweep:
    def look() -> Sweep:
        return sweep_marked(ATTEMPT_ENV, attempt_id, account=account)

    return confirm_gone(look(), look)


def _refuse(ledger: OwnershipLedger, task_id: str, why: str) -> NoReturn:
    ledger.note_refusal(task_id, why)
    raise TakeoverRefused(f"{task_id}: {why}; the card stays held, retry after they are gone")
