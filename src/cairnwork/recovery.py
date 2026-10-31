"""Restart recovery (preserved behavior #21): settle what a crash left behind.

Rules (from Nexus-new's experience, see docs/LESSONS_FROM_NEXUS.md):

- run only when no Cairnwork run is in progress: recovery needs the exclusive
  run lock (runlock.py), which the kernel releases when a run dies;
- never re-run an interrupted attempt. A task becomes COMPLETED only if Core
  already recorded a verdict that is verified for the candidate the journal
  names and the run had not already decided FAILED; every other in-flight
  task becomes FAILED with an INTERRUPTED reason.
  To try again, supersede it and submit a new key (pipeline.py);
- kill any worker the dead run left behind (found by its CAIRNWORK_ATTEMPT
  environment marker), look again until they are shown gone (``confined.confirm_gone``: a
  delivered SIGKILL is not a death), and remove the attempt's clones. A worker that ran as a
  worker account (journaled at WORKER_STARTED) is shown gone only when that account is idle.
  When they are not shown gone (the process table cannot be read: SWEEP_UNAVAILABLE; or
  survivors, hidden environments, a busy account) the Decision says so (``sweep_unavailable``,
  detail with the reason's code) and the recovery is not clean (route exits 4, as for leftover
  directories). The status decision does not change: a verdict Core recorded is bound to the
  candidate commit, and a process that outlived the run can neither alter it nor record
  another, so COMPLETED still stands; a task without such a verdict is FAILED either way
  (#189). A FAILED task is then also marked unswept, in the ledger transaction that makes it
  FAILED: it cannot be superseded, so its card cannot be taken over by a new attempt, until
  takeover.supersede has shown the processes gone (#244);
- macOS withholds the environment of Apple platform binaries (/bin/sh, /bin/sleep), so their
  marker cannot be seen: such an owner process is killed when its working directory is inside
  one of the attempt's own run directories (``_cwd_dirs``, proc_cwd, #301); a run ended by
  KeyboardInterrupt or SystemExit leaves them in place for this (#537). One that changed
  directory out of them is not found, nor one left after a crash during recovery removed them;
- act only inside the state's own run root: the journal is a file, so a run root its
  DISPATCHED row names that is not the state's is not cleaned or searched for processes, only
  re-scanned for names (reported, ``JOURNAL_RUN_ROOT_NOT_USED``), and an attempt id a run does
  not make (``a`` + 16 hex, pipeline.py) names no directory (``ATTEMPT_ID_NOT_A_RUN_ID``);
- settle each task on its own: one bad record is reported, not fatal, and
  recovery is idempotent, so it can run again after a crash during recovery.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path

from cairnwork import runlock, unswept
from cairnwork.confined import ATTEMPT_ENV, SWEEP_UNAVAILABLE, confirm_gone, sweep_marked
from cairnwork.journal import Journal, dispatch_mark
from cairnwork.notary import Notary
from cairnwork.ownership import OWNER, OwnershipLedger, OwnershipRecord
from cairnwork.proc_cwd import attempt_dirs
from cairnwork.reasons import _names
from cairnwork.verifier_account import VerifierAccount, VerifierAccountError
from cairnwork.workspace import WorkspaceError, remove_attempt_clone


@dataclass(frozen=True)
class Decision:
    task_id: str
    attempt_id: str
    previous_status: str
    new_status: str
    reason: str
    killed_processes: int = 0  # first sweep; SWEEP_UNAVAILABLE (-1) when the table was unread
    detail: str = ""  # e.g. "LEFTOVER_RUN_DIRS: 2: a1-q-v0, a1-r-v0.x"; "" when nothing remains
    leftover_run_dirs: int = 0  # how many run directories the cleanup could not remove
    sweep_unavailable: bool = False  # the attempt's processes are not shown gone: may still run


def recover(
    *,
    ledger: OwnershipLedger,
    notary: Notary,
    journal: Journal,
    run_root: Path,
    verifier_account: VerifierAccount | None = None,
    leftovers_log: Path | None = None,
    worker_account: str | None = None,
) -> list[Decision]:
    """Settle every BOUND or DISPATCHED task. Raises RunLockBusy if a run is active.
    ``worker_account``: the state's agy_run_as_user, assumed for a worker journaled before #244
    (its WORKER_STARTED names no account); with none, such an attempt is fenced as unknown.
    Directories that could not be removed go in the Decision and, when ``leftovers_log`` is
    given, in that append-only JSON-lines file; never in the journal (a journal row would
    change what a later recovery decides)."""
    decisions = []
    # Hold the account's lock across the whole settle loop: a dead run's verifier ran as that
    # account, and settling removes its leftover run directories (owned by the account) with
    # account.remove, so no other run may touch the account meanwhile.
    account_lock = verifier_account.exclusive() if verifier_account is not None else nullcontext()
    with runlock.held(ledger.path, exclusive=True), account_lock:
        if verifier_account is not None:
            verifier_account.require_idle()
        for record in ledger.in_flight(include_foreign=True):
            if record.owner != OWNER:  # not ours: report it, never settle it
                decisions.append(
                    Decision(record.task_id, "", record.status, record.status, "FOREIGN_OWNER")
                )
                continue
            try:
                decisions.append(
                    _settle(
                        record,
                        ledger,
                        notary,
                        journal,
                        run_root,
                        verifier_account,
                        leftovers_log,
                        worker_account,
                    )
                )
            except Exception as exc:  # noqa: BLE001 - one bad record must not stop the others
                decisions.append(
                    Decision(
                        record.task_id,
                        "",
                        record.status,
                        record.status,
                        f"RECOVERY_ERROR: {type(exc).__name__}: {exc}",
                    )
                )
    return decisions


def _settle(
    record: OwnershipRecord, ledger, notary, journal, run_root, account=None, leftovers_log=None,
    configured=None,
) -> Decision:  # fmt: skip
    entries = journal.entries(record.task_id)
    if not entries and record.status == "DISPATCHED":
        # the journal is written before DISPATCHED, so this is the wrong or a lost journal
        raise RuntimeError("DISPATCHED task has no journal entries; is this the right journal?")
    if not entries:  # still BOUND: the ledger moves to DISPATCHED before anything is spawned
        ledger.set_status(record.task_id, "FAILED", started_nothing=True)  # no legacy to sweep
        return Decision(record.task_id, "", record.status, "FAILED", "INTERRUPTED_BEFORE_DISPATCH")
    attempt_id = entries[-1].attempt_id
    phases = [e for e in entries if e.attempt_id == attempt_id]
    ran_as = unswept.journaled_account(phases) or ""  # "" the owner's uid (or no worker)
    if ran_as == unswept.UNKNOWN_ACCOUNT and configured:
        ran_as = configured  # journaled before #244: assume the configured worker account
    worker = None if ran_as in ("", unswept.UNKNOWN_ACCOUNT) else ran_as

    mark = dispatch_mark(phases)  # a hidden process older than the attempt is not its own
    dispatched = [e.detail for e in phases if e.phase == "DISPATCHED" and e.detail]
    roots, other = _owner_roots(run_root, dispatched)
    dirs = _cwd_dirs(roots, account, attempt_id)  # before the cleanup below removes them

    def look():  # the worker account it ran as, if any (pipeline.py journals it)
        return sweep_marked(ATTEMPT_ENV, attempt_id, account=worker, dispatched=mark,
                            cwd_dirs=dirs)  # fmt: skip

    first = look()
    swept = confirm_gone(first, look)
    unknown = ran_as == unswept.UNKNOWN_ACCOUNT  # its account's processes cannot be checked
    unchecked = unknown or not swept.gone  # unknown or alive, never "none" (confined.Sweep)
    left = []
    for owner_root in roots:
        _remove_leftovers(owner_root, attempt_id)
    if account is not None:  # the verifier ran under the account's run root; the owner cannot rm it
        _remove_account_leftovers(account, attempt_id)
    for owner_root in roots:
        left += _leftover_names(owner_root, attempt_id)
    if account is not None:
        left += _leftover_names(account.run_root, attempt_id)
    if other is not None:  # names only: whatever is there was not touched
        left += _leftover_names(other, attempt_id)
    notes = [swept.problem() or _UNKNOWN_ACCOUNT] if unchecked else []
    notes += [_OTHER_ROOT] if other is not None else []
    notes += [] if _run_attempt(attempt_id) else [_NOT_A_RUN_ID]
    if left:
        notes.append(f"LEFTOVER_RUN_DIRS: {_names(left)}")
    detail = "; ".join(notes)
    if left and leftovers_log is not None:
        try:
            _log_leftovers(leftovers_log, record.task_id, attempt_id, left)
        except OSError:
            detail += " (leftover log write failed)"  # the Decision still carries the signal
    candidates = [e.detail for e in phases if e.phase == "CANDIDATE"]
    finished = [e.detail for e in phases if e.phase == "FINISHED" and e.detail in _RESULTS]
    verified = False
    if candidates and finished[-1:] != ["FAILED"]:  # the run's own FAILED decision stands
        verdict = notary.verdict(record.task_id, attempt_id, expected_candidate=candidates[-1])
        verified = bool(verdict and verdict.verified)
    if verified:
        if record.status == "BOUND":
            ledger.set_status(record.task_id, "DISPATCHED")
        new, reason = "COMPLETED", "CORE_VERIFIED_BEFORE_CRASH"
    else:
        new, reason = "FAILED", f"INTERRUPTED_AFTER_{phases[-1].phase}"
    fence = (attempt_id, ran_as) if unchecked and new == "FAILED" else None  # #244
    ledger.set_status(record.task_id, new, fence=fence)  # the mark is in the FAILED transaction
    killed = first.killed if first.listed else SWEEP_UNAVAILABLE
    return Decision(
        record.task_id, attempt_id, record.status, new, reason, killed, detail, len(left),
        unchecked,
    )  # fmt: skip


def _owner_roots(run_root, dispatched: list[str]) -> tuple[list[Path], Path | None]:
    """The owner-side roots recovery cleans and searches: the state's run root only. The run
    journals the resolved run root at DISPATCHED (pipeline.py); the journal is a file, so a
    root it names that is not the state's (by realpath) is returned apart, never acted on."""
    if not dispatched:
        return [Path(run_root)], None
    try:
        same = os.path.realpath(dispatched[-1]) == os.path.realpath(run_root)
    except (OSError, ValueError):
        same = False
    return [Path(run_root)], None if same else Path(dispatched[-1])


def _run_attempt(attempt_id) -> bool:
    """An id a run makes (pipeline.py, verify_candidate.py: ``a`` + 16 hex). Only such an id
    names directories or working directories here: the journal is a file, do not trust it for
    paths, and a looser id could name another entry of the run root (``a1-q-v0``)."""
    return isinstance(attempt_id, str) and _RUN_ATTEMPT_ID.fullmatch(attempt_id) is not None


def _cwd_dirs(roots, account, attempt_id: str) -> tuple[str, ...]:
    """The attempt's own run directories (realpaths) under the owner's roots and the verifier
    account's run root, whose verifier checkouts the owner makes: an owner process whose
    environment macOS withholds is tied to the attempt when its cwd is inside one (#301)."""
    if not _run_attempt(attempt_id):
        return ()
    extra = [account.run_root] if account is not None else []
    return tuple(str(d) for d in attempt_dirs([*roots, *extra], attempt_id))


def _log_leftovers(path: Path, task_id: str, attempt_id: str, left: list[str]) -> None:
    """Append one JSON line (0600, never following a symlink): time, task, attempt, count, names."""
    row = {"time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "task_id": task_id,
           "attempt_id": attempt_id, "count": len(left), "names": _names(left)}  # fmt: skip
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "a") as out:
        os.fchmod(out.fileno(), 0o600)
        out.write(json.dumps(row, sort_keys=True) + "\n")


_UNKNOWN_ACCOUNT = (
    "SWEEP_UNAVAILABLE: the account the worker ran as is unknown (journaled before #244)"
    " and agy_run_as_user is not set"
)
_OTHER_ROOT = (
    "JOURNAL_RUN_ROOT_NOT_USED: the journal names a run root that is not the state's;"
    " nothing there was removed or swept, its names are listed"
)
_NOT_A_RUN_ID = "ATTEMPT_ID_NOT_A_RUN_ID: no directory was looked for"
_RUN_ATTEMPT_ID = re.compile(r"a[0-9a-f]{16}")
_RESULTS = ("COMPLETED", "FAILED")  # the run's own FINISHED details (pipeline.py)
_RUN_TAGS = "vbpqre"  # verify, base-test, P2P probe / candidate run / re-run, base env (p2p.py)


def _run_dirs(root: Path, attempt_id: str) -> tuple[list[Path], list[Path], list[Path]]:
    """The attempt's run directories under ``root``: verifier checkouts (``.work``), private
    run dirs (``.priv``...; a verify log stays as evidence, only its run/ goes) and clones.
    One walk for the owner side and the verifier-account side, so the two cannot drift."""
    pattern = f"{attempt_id}-[{_RUN_TAGS}]-v*"

    # symlinks are listed too (dangling or not): they are unlinked, never followed (_remove_inside)
    def real(p: Path) -> bool:
        return p.is_symlink() or p.is_dir()

    works = [w for w in root.glob(f"{pattern}.work") if real(w)]
    privates = []
    for private in root.glob(f"{pattern}.*"):  # verifier, base-test and P2P runs
        if private.suffix == ".work" or not real(private):
            continue
        # a base-test or P2P run is not Core evidence and would show the tests: all of it goes
        keep_log = f"{attempt_id}-v-" in private.name and not private.is_symlink()
        privates.append(private / "run" if keep_log else private)
    clones = sorted(p for p in root.glob(pattern) if real(p) and "." not in p.name)
    return works, privates, clones


def _remove_leftovers(run_root: Path, attempt_id: str) -> None:
    """Attempt and verifier clones, a snapshot index, and verifier scratch (run/) dirs.
    Verifier logs (the private dirs themselves) are kept as evidence."""
    if not _run_attempt(attempt_id):  # the journal is a file: do not trust it for paths
        return
    root = Path(run_root)
    (root / f"{attempt_id}.index").unlink(missing_ok=True)
    (root / f"{attempt_id}.tests.index").unlink(missing_ok=True)
    works, privates, clones = _run_dirs(root, attempt_id)
    for work in works:  # a verifier checkout's own dir
        if not work.is_symlink():
            try:
                remove_attempt_clone(work, work.name.removesuffix(".work"))
            except WorkspaceError:
                pass
        _remove_inside(root, work)
    for private in privates:
        _remove_inside(root, private)
    for name in [attempt_id] + [p.name for p in clones]:
        path = root / name
        if path.is_symlink():
            _unlink(path)
        elif path.is_dir():
            try:
                remove_attempt_clone(root, name)
            except WorkspaceError:
                pass  # a leftover directory is disk space, not a wrong status


def _unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass  # the re-scan reports what is still there


def _remove_inside(root: Path, target: Path) -> None:
    """Delete ``target`` only if it is strictly inside ``root``. A symlink is unlinked itself,
    never followed: a name the dead run (or its account) swapped must not aim rmtree elsewhere."""
    if target.is_symlink():
        _unlink(target)
        return
    real = Path(os.path.realpath(target))
    if Path(os.path.realpath(root)) in real.parents:
        shutil.rmtree(target, ignore_errors=True)


def _remove_account_leftovers(account: VerifierAccount, attempt_id: str) -> None:
    """Remove the attempt's verifier run directories under the account's OWN run root.

    When a verifier account is used the verifier clones and scratch live under
    ``account.run_root`` and are owned by the account, so only ``account.remove`` (chmod + rm
    as that account) can delete them — the owner-side ``_remove_leftovers`` cannot. This mirrors
    normal cleanup (verifiers.py removes the same dirs with ``account.remove``) and keeps the
    verify run's log directory as evidence, as ``_remove_leftovers`` does."""
    if not _run_attempt(attempt_id):  # the journal is a file: do not trust it for paths
        return
    root = account.run_root
    if not root.is_dir():
        return
    works, privates, clones = _run_dirs(root, attempt_id)
    targets = works + privates + clones
    for target in targets:
        try:
            account.remove(target)
        except VerifierAccountError:
            pass  # the re-scan after cleanup reports what is still there (_leftover_names)
        # the verifier checkout and its work dir are the OWNER's (verifiers.py makes them), so
        # the account's rm cannot remove them: the owner finishes what the account could not
        _remove_inside(root, target)


def _leftover_names(root: Path, attempt_id: str) -> list[str]:
    """Names (only) of the attempt's directories still under ``root`` after cleanup: anything
    that can hold laid-over hidden tests. A verify run's private dir is evidence and does not
    count, its ``run/`` scratch does. Re-scans from disk; it trusts nothing the removers said."""
    if not _run_attempt(attempt_id) or not Path(root).is_dir():
        return []
    root = Path(root)
    works, privates, clones = _run_dirs(root, attempt_id)
    own = [root / attempt_id] if os.path.lexists(root / attempt_id) else []
    names = {
        p.name if p.name != "run" else p.parent.name + "/run"
        for p in works + privates + clones + own
        if os.path.lexists(p)  # a dangling symlink is still a leftover
    }
    return sorted(names)
