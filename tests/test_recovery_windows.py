"""#244: two recovery crash windows a TLA+ pilot flagged, verified here. Recovery is unchanged.

1. Parent crash (SIGKILL mid-worker), process sweep unavailable, recovery marks the task FAILED,
   the operator supersedes and resubmits: the child the dead run's worker started could still
   act while the new attempt runs. CONFIRMED (PR #245), FIXED: the takeover is refused until the
   attempt's processes are shown gone (takeover.py, ownership.py TakeoverRefused); pinned below.
   The signal is still the Decision's ``sweep_unavailable`` (route ``recover`` exits 4, #189).
2. Core recorded a verified verdict, then an exception (here: the verdict readback fails) and a
   crash before the FINISHED journal row: the live run would have said FAILED (INFRA_ERROR), but
   recovery says COMPLETED. Reproduced; INTENDED per recovery.py: a Core verdict verified for the
   journaled candidate is the authority, and only a *journaled* FAILED decision overrides it.
"""

import os
import signal
import sqlite3
import subprocess
import sys
import time

import pytest
from conftest import git
from test_pipeline import VERIFY, EditWorker
from test_recovery import DRIVER, GOOD, settle, stores
from test_recovery import project as project  # noqa: F401 - fixture

import cairnwork.recovery as recovery
from cairnwork import takeover
from cairnwork.confined import UNLISTED
from cairnwork.journal import Journal
from cairnwork.notary import Notary
from cairnwork.ownership import DuplicateLogicalTask, OwnershipLedger, TakeoverRefused
from cairnwork.pipeline import TaskSpec, run_task

pytestmark = pytest.mark.sandbox


def _spec(project, key):  # noqa: F811 - the crash driver's task (tests/crash_driver.py)
    base = git(project, "rev-parse", "HEAD")
    return TaskSpec(
        key, "card-k1", "fix add", "add() is off by one", project, base, ("src/calc.py",), VERIFY
    )


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _survivor_pid(state):
    path = state / "survivor.pid"
    deadline = time.monotonic() + 30
    while not path.exists() or not path.read_text():
        assert time.monotonic() < deadline, "the worker's child never started"
        time.sleep(0.05)
    return int(path.read_text())


def test_a_survivor_of_an_unswept_crash_blocks_the_takeover_until_it_is_swept(
    project,  # noqa: F811
    tmp_path,
    monkeypatch,
):
    """#244 window 1 (recovery.py _settle, runlock.py: children do not hold the run lock).
    The dead run's own worker started the child; the run is SIGKILLed mid-worker. The takeover
    (supersede, then a new key) is refused while the child is unchecked, and goes ahead once a
    real sweep has killed it (takeover.py)."""
    proc = subprocess.Popen(
        [sys.executable, str(DRIVER), str(project), str(tmp_path), GOOD, "survivor"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    pid = None
    try:
        deadline = time.monotonic() + 60
        while not (tmp_path / "worker-running").exists():
            assert time.monotonic() < deadline and proc.poll() is None
            time.sleep(0.1)
        pid = _survivor_pid(tmp_path)
        os.kill(proc.pid, signal.SIGKILL)
        proc.wait()
        monkeypatch.setattr(recovery, "sweep_marked", lambda *_a, **_k: UNLISTED)
        (decision,), ledger = settle(tmp_path)
        assert (decision.new_status, decision.reason, decision.sweep_unavailable) == (
            "FAILED",
            "INTERRUPTED_AFTER_WORKER_STARTED",
            True,
        )
        assert _alive(pid)  # recovery could not stop it, and said so
        with pytest.raises(TakeoverRefused):  # a raw writer is fenced too
            ledger.set_status(decision.task_id, "SUPERSEDED")
        real_sweep = takeover.sweep_marked
        monkeypatch.setattr(takeover, "sweep_marked", lambda *_a, **_k: UNLISTED)
        with pytest.raises(TakeoverRefused, match="SWEEP_UNAVAILABLE"):
            takeover.supersede(ledger, decision.task_id)
        assert ledger.get(task_id=decision.task_id).status == "FAILED" and _alive(pid)
        _, notary, journal = stores(tmp_path)
        with pytest.raises(DuplicateLogicalTask):  # the card is still held: no new attempt
            run_task(
                _spec(project, "k2"),
                ledger=ledger,
                notary=notary,
                journal=journal,
                worker=EditWorker({"src/calc.py": GOOD}),
                run_root=tmp_path / "runs",
            )
        monkeypatch.setattr(takeover, "sweep_marked", real_sweep)  # the process table is readable
        assert takeover.supersede(ledger, decision.task_id).status == "SUPERSEDED"
        result = run_task(  # at once: supersede itself must have shown the child gone
            _spec(project, "k2"),
            ledger=ledger,
            notary=notary,
            journal=journal,
            worker=_SignallingWorker(tmp_path, {"src/calc.py": GOOD}),
            run_root=tmp_path / "runs",
        )
        assert result.status == "COMPLETED" and result.verified, result.reason
        assert result.attempt_id != decision.attempt_id
        # the child copies the new worker's token within ~50 ms if it is alive: this fails if
        # the takeover released the card while the old attempt's child still ran
        assert not (tmp_path / "old-attempt-wrote").exists()
        assert not _alive(pid)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        if pid is not None and _alive(pid):
            os.kill(pid, signal.SIGKILL)


class _SignallingWorker(EditWorker):
    """The new attempt's worker: tells a surviving child of the old attempt to act
    (survivor_child.py), then gives it a second to do so."""

    def __init__(self, state, files):
        super().__init__(files)
        self.state = state

    def run(self, workdir, task):
        super().run(workdir, task)
        part = self.state / "new-attempt-running.part"
        part.write_text(workdir.name)
        os.replace(part, self.state / "new-attempt-running")
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline and not (self.state / "old-attempt-wrote").exists():
            time.sleep(0.05)


class _ReadbackFails(Notary):
    """Core records the verdict; reading it back then fails (e.g. a locked or broken disk)."""

    def verdict(self, task_id, attempt_id, *, expected_candidate):
        raise sqlite3.OperationalError("disk I/O error")


class _SimulatedCrash(BaseException):
    """Not an Exception: nothing in the pipeline catches it, like a process death."""


class _CrashBeforeFinished(Journal):
    def record(self, task_id, attempt_id, phase, detail=""):
        if phase == "FINISHED":
            raise _SimulatedCrash(detail)
        super().record(task_id, attempt_id, phase, detail)


def _run_good_fix(project, tmp_path, journal):  # noqa: F811
    return run_task(
        _spec(project, "k1"),
        ledger=OwnershipLedger(tmp_path / "own.sqlite"),
        notary=_ReadbackFails(tmp_path / "notary.sqlite"),
        journal=journal,
        worker=EditWorker({"src/calc.py": GOOD}),
        run_root=tmp_path / "runs",
    )


def test_an_error_after_the_core_verdict_fails_the_live_run(project, tmp_path):  # noqa: F811
    """#244 window 2, no crash: the live run decides FAILED and journals it."""
    result = _run_good_fix(project, tmp_path, Journal(tmp_path / "journal.sqlite"))
    assert result.status == "FAILED" and not result.verified
    assert result.reason.startswith("INFRA_ERROR: OperationalError"), result.reason
    ledger, notary, journal = stores(tmp_path)
    assert ledger.get(task_id=result.task_id).status == "FAILED"
    last = journal.entries(result.task_id)[-1]
    assert (last.phase, last.detail) == ("FINISHED", "FAILED")
    verdict = notary.verdict(
        result.task_id, result.attempt_id, expected_candidate=result.candidate_commit or ""
    )
    assert verdict is not None and verdict.verified  # Core did verify the candidate


def test_a_crash_between_that_error_and_finished_recovers_completed(project, tmp_path):  # noqa: F811
    """#244 window 2 with a crash before FINISHED: recovery trusts the Core verdict."""
    with pytest.raises(_SimulatedCrash, match="FAILED"):  # the run had decided FAILED
        _run_good_fix(project, tmp_path, _CrashBeforeFinished(tmp_path / "journal.sqlite"))
    ledger, notary, journal = stores(tmp_path)
    (task,) = ledger.in_flight()
    assert task.status == "DISPATCHED"
    phases = [e.phase for e in journal.entries(task.task_id)]
    assert phases[-1] == "NOTARIZING" and "FINISHED" not in phases
    decisions, _ = settle(tmp_path)
    assert [(d.new_status, d.reason) for d in decisions] == [
        ("COMPLETED", "CORE_VERIFIED_BEFORE_CRASH")
    ]
    assert ledger.get(task_id=task.task_id).status == "COMPLETED"


class _LedgerFailsOnTheFinalMove(OwnershipLedger):
    """The ledger write that ends the attempt fails (e.g. a locked or broken disk)."""

    def set_status(self, task_id, status, **kwargs):
        if status in ("COMPLETED", "FAILED"):
            raise sqlite3.OperationalError("database is locked")
        return super().set_status(task_id, status, **kwargs)


def test_an_error_in_the_final_ledger_move_propagates_and_leaves_the_task_in_flight(  # noqa: F811
    project, tmp_path
):
    """#245 review N1 (pipeline.py _execute, finally): the move is not swallowed into a status.
    The task stays DISPATCHED with FINISHED journaled, so restart recovery settles it."""
    journal = Journal(tmp_path / "journal.sqlite")
    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        run_task(
            _spec(project, "k1"),
            ledger=_LedgerFailsOnTheFinalMove(tmp_path / "own.sqlite"),
            notary=Notary(tmp_path / "notary.sqlite"),
            journal=journal,
            worker=EditWorker({"src/calc.py": GOOD}),
            run_root=tmp_path / "runs",
        )
    ledger = OwnershipLedger(tmp_path / "own.sqlite")
    (task,) = ledger.in_flight()
    assert task.status == "DISPATCHED"
    assert journal.entries(task.task_id)[-1].phase == "FINISHED"
