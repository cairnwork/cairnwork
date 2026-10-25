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

A refusal names what keeps the fence (``Sweep.kept_by``: pids with a fixed label each, never
text a process supplies), so the operator can stop those processes or wait for them, then run
the takeover again.

The account an attempt ran as is in its mark. A mark made from a journal entry from before #244
names UNKNOWN_ACCOUNT (unswept.py): ``account`` (the state's configured worker account) is then
assumed; with none configured the takeover is refused until an operator sets it.

A LEGACY mark (made at the ledger migration; the attempt is unknown to the ledger) needs the
journal: every attempt it names for the task is swept, each with the account its WORKER_STARTED
entry names (UNKNOWN_ACCOUNT: as above). The processes carry the attempt id only, never the
task id, so a task the journal names no attempt for cannot be swept at all (the journal may be
missing, recreated or restored): refused (LEGACY_ATTEMPT_UNKNOWN), for a maintainer by hand.
"""

from __future__ import annotations

from typing import NoReturn

from cairnwork import unswept
from cairnwork.confined import ATTEMPT_ENV, Sweep, confirm_gone, sweep_marked
from cairnwork.journal import Journal, dispatch_mark
from cairnwork.ownership import OwnershipLedger, OwnershipRecord, TakeoverRefused


def supersede(
    ledger: OwnershipLedger,
    task_id: str,
    *,
    journal: Journal | None = None,
    account: str | None = None,
) -> OwnershipRecord:
    """Mark the task SUPERSEDED. Raises TakeoverRefused while its attempt's processes are not
    shown gone; InvalidStatusTransition as ``ledger.set_status`` does. ``account``: the state's
    configured worker account, assumed where the attempt's own is unknown."""
    held = ledger.unswept_attempt(task_id)
    if held is None:
        return ledger.set_status(task_id, "SUPERSEDED")
    entries = journal.entries(task_id) if journal is not None else []
    if held.attempt_id != unswept.LEGACY:
        attempts = [(held.attempt_id, held.account)]
    else:
        attempts = _legacy_attempts(ledger, task_id, entries)
    for attempt_id, ran_as in attempts:
        if ran_as == unswept.UNKNOWN_ACCOUNT and not account:
            why = f"the account attempt {attempt_id} ran as is unknown (journaled before #244)"
            _refuse(ledger, task_id, f"SWEEP_UNAVAILABLE: {why} and agy_run_as_user is not set")
        mark = dispatch_mark([e for e in entries if e.attempt_id == attempt_id])
        found = _swept(attempt_id, account if ran_as == unswept.UNKNOWN_ACCOUNT else ran_as, mark)
        if not found.gone:
            kept = found.kept_by()
            keeping = f"; kept by: {kept}" if kept else ""
            _refuse(ledger, task_id, f"{found.problem()} (attempt {attempt_id}){keeping}")
    return ledger.set_status(task_id, "SUPERSEDED", swept=held.attempt_id)


def _legacy_attempts(ledger: OwnershipLedger, task_id: str, entries) -> list[tuple[str, str]]:
    """(attempt id, account it ran as) of every attempt the journal names for the task."""
    attempts = sorted({e.attempt_id for e in entries if e.attempt_id})  # "" marks nothing
    if not attempts:
        why = "its processes carry no task id, so none can be swept (a maintainer checks by hand)"
        _refuse(ledger, task_id, f"LEGACY_ATTEMPT_UNKNOWN: no journaled attempt; {why}")
    return [
        (a, unswept.journaled_account([e for e in entries if e.attempt_id == a]) or "")
        for a in attempts
    ]


def _swept(attempt_id: str, account: str | None, mark: str) -> Sweep:
    def look() -> Sweep:  # mark: the attempt's DISPATCHED boot mark (journal), "" if unknown
        return sweep_marked(ATTEMPT_ENV, attempt_id, account=account or None, dispatched=mark)

    return confirm_gone(look(), look)


def _refuse(ledger: OwnershipLedger, task_id: str, why: str) -> NoReturn:
    ledger.note_refusal(task_id, why)
    raise TakeoverRefused(f"{task_id}: {why}; the card stays held, retry after they are gone")
