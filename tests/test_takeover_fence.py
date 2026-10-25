"""#244 window 1, the ledger side of the takeover fence (unswept.py, ownership.py set_status).

- The mark is written in the transaction that moves the task to FAILED: a failure (or a crash)
  in the mark write leaves the task where it was, never FAILED without its mark.
- A ledger from before the fence marks every FAILED task LEGACY (the ledger cannot tell which
  one recovery left unswept); the takeover then sweeps the journal's attempts.
- A live run whose worker cannot show its processes gone (agy WORKER_SWEEP_UNCLEAN) is fenced
  too, with the worker account it ran as, and that account is journaled at WORKER_STARTED.
The sweep's answers are faked (no other account, no sudo)."""

import sqlite3

import pytest
from test_pipeline import EditWorker, task
from test_recovery import crash, settle, stores
from test_recovery import project as project  # noqa: F401 - fixture

import cairnwork.recovery as recovery
from cairnwork import ownership, takeover, unswept
from cairnwork.confined import UNLISTED, Sweep
from cairnwork.intake import Intake
from cairnwork.intake_config import DEFAULT_CONFIG
from cairnwork.journal import Journal
from cairnwork.ownership import OWNER, InvalidOwnershipValue, OwnershipLedger, TakeoverRefused
from cairnwork.pipeline import run_task
from cairnwork.unswept import Mark, WorkerMayLive

GONE = Sweep(listed=True)
ATTEMPT = "a0123456789abcdef"


class _MarkFails(Exception):
    pass


class _Crash(BaseException):
    """Not an Exception: like a process death inside the mark write."""


def _fail_marks(monkeypatch, error):
    def mark(*_a, **_k):
        raise error("the mark write failed")

    monkeypatch.setattr(unswept, "mark", mark)


@pytest.mark.parametrize("error", [_MarkFails, _Crash])
def test_recovery_never_leaves_failed_without_its_mark(project, tmp_path, monkeypatch, error):  # noqa: F811
    """#244 review: a mutant that writes FAILED before (or apart from) the mark dies here."""
    crash(project, tmp_path, "CANDIDATE")
    monkeypatch.setattr(recovery, "sweep_marked", lambda *_a, **_k: UNLISTED)
    _fail_marks(monkeypatch, error)
    ledger = stores(tmp_path)[0]
    (task,) = ledger.in_flight()
    if error is _Crash:
        with pytest.raises(_Crash):
            settle(tmp_path)
    else:
        (decision,), _ = settle(tmp_path)
        assert decision.reason.startswith("RECOVERY_ERROR")
    assert ledger.get(task_id=task.task_id).status == "DISPATCHED"  # still in flight
    assert ledger.unswept_attempt(task.task_id) is None
    monkeypatch.undo()
    monkeypatch.setattr(recovery, "sweep_marked", lambda *_a, **_k: UNLISTED)
    (decision,), _ = settle(tmp_path)  # recovery is idempotent: the next one fences it
    assert ledger.get(task_id=task.task_id).status == "FAILED"
    assert ledger.unswept_attempt(task.task_id) == Mark(decision.attempt_id, "", "")


def _dispatched(tmp_path):
    ledger = OwnershipLedger(tmp_path / "own.sqlite")
    task_id = ledger.bind("k1", {"a": 1}, OWNER, "card-k1").record.task_id
    ledger.set_status(task_id, "DISPATCHED")
    return ledger, task_id


def test_a_fence_goes_only_with_failed_and_a_supersede_must_name_the_attempt(tmp_path):
    ledger, task_id = _dispatched(tmp_path)
    with pytest.raises(InvalidOwnershipValue):
        ledger.set_status(task_id, "COMPLETED", fence=("a1", ""))
    assert ledger.unswept_attempt(task_id) is None
    ledger.set_status(task_id, "FAILED", fence=("a1", ""))
    with pytest.raises(TakeoverRefused, match="UNSWEPT_ATTEMPT"):
        ledger.set_status(task_id, "SUPERSEDED", swept="a2")  # another attempt was swept
    assert ledger.set_status(task_id, "SUPERSEDED", swept="a1").status == "SUPERSEDED"
    assert ledger.unswept_attempt(task_id) is None  # dropped with the move


def _legacy_ledger(tmp_path):
    """A ledger built by the schema before #244 (no unswept_attempt table)."""
    path = tmp_path / "own.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(ownership._SCHEMA)
    rows = [("k1", "d1", OWNER, "task-f", "c1", "FAILED"),
            ("k2", "d2", OWNER, "task-c", "c2", "COMPLETED"),
            ("k3", "d3", OWNER, "task-n", "c3", "FAILED"),
            ("k4", "d4", OWNER, "task-d", "c4", "DISPATCHED"),
            ("k5", "d5", OWNER, "task-b", "c5", "BOUND")]  # fmt: skip
    conn.executemany("INSERT INTO task_ownership VALUES (?, ?, ?, ?, ?, ?)", rows)
    conn.commit()
    conn.close()
    journal = Journal(tmp_path / "journal.sqlite")
    journal.record("task-f", ATTEMPT, "DISPATCHED", str(tmp_path / "runs"))
    journal.record("task-f", ATTEMPT, "WORKER_STARTED")  # before #244: no account named
    journal.record("task-f", ATTEMPT, "FINISHED", "FAILED")
    return path, journal


def _sweeps(monkeypatch, *answers):
    calls = []

    def fake(var, value, **kw):
        calls.append((value, kw.get("account")))
        return answers[min(len(calls), len(answers)) - 1]

    monkeypatch.setattr(takeover, "sweep_marked", fake)
    monkeypatch.setattr("cairnwork.confined.CONFIRM_WAIT", 0)
    return calls


def test_a_legacy_ledger_fences_every_failed_and_in_flight_task(tmp_path, monkeypatch):
    """Codex #244 MUST-FIX 2 and round 2 #4: FAILED tasks from before the upgrade, and tasks an
    old runner may still end FAILED, supersede only after a sweep."""
    path, journal = _legacy_ledger(tmp_path)
    ledger = OwnershipLedger(path)
    for legacy in ("task-f", "task-n", "task-d", "task-b"):
        assert ledger.unswept_attempt(legacy) == Mark(unswept.LEGACY, "", "")
    assert ledger.unswept_attempt("task-c") is None  # COMPLETED never needs a takeover
    with pytest.raises(TakeoverRefused, match="UNSWEPT_ATTEMPT"):
        ledger.set_status("task-f", "SUPERSEDED")
    calls = _sweeps(monkeypatch, GONE)
    with pytest.raises(TakeoverRefused, match="LEGACY_ATTEMPT_UNKNOWN"):
        takeover.supersede(ledger, "task-f")  # no journal: the attempt is unknown
    # its worker started before #244, so the account it ran as is unknown: none configured
    with pytest.raises(TakeoverRefused, match="SWEEP_UNAVAILABLE: .*agy_run_as_user is not set"):
        takeover.supersede(ledger, "task-f", journal=journal)
    assert not calls
    calls = _sweeps(monkeypatch, Sweep(True, account=1))
    with pytest.raises(TakeoverRefused, match="WORKER_ACCOUNT_BUSY"):
        takeover.supersede(ledger, "task-f", journal=journal, account="cw-worker")
    assert calls[0] == (ATTEMPT, "cw-worker")  # the journal's attempt, the configured account
    _sweeps(monkeypatch, GONE)
    assert takeover.supersede(ledger, "task-f", journal=journal, account="cw-worker").status == (
        "SUPERSEDED"
    )


@pytest.mark.parametrize("journal_state", ["kept", "missing", "recreated"])
def test_a_legacy_task_with_no_journaled_attempt_stays_fenced(tmp_path, monkeypatch, journal_state):
    """Codex #244 round 2 #2: the processes carry the attempt id, not the task id, so a task
    the journal names no attempt for cannot be swept, whatever the journal's age (it may be a
    restored backup)."""
    path, journal = _legacy_ledger(tmp_path)
    ledger = OwnershipLedger(path)
    calls = _sweeps(monkeypatch, GONE)
    if journal_state != "kept":
        (tmp_path / "journal.sqlite").unlink()
        journal = Journal(tmp_path / "journal.sqlite")
        if journal_state == "recreated":
            journal.record("task-x", "a9", "DISPATCHED", "")
    with pytest.raises(TakeoverRefused, match="LEGACY_ATTEMPT_UNKNOWN: no journaled attempt"):
        takeover.supersede(ledger, "task-n", journal=journal, account="cw-worker")
    assert ledger.get(task_id="task-n").status == "FAILED" and not calls
    held = ledger.unswept_attempt("task-n")
    assert held is not None and held.refusal.startswith("LEGACY_ATTEMPT_UNKNOWN")


def test_intake_refuses_a_legacy_task_with_no_journaled_attempt(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    path, _ = _legacy_ledger(tmp_path)
    path.rename(state / "ownership.sqlite")
    (tmp_path / "journal.sqlite").rename(state / "journal.sqlite")
    monkeypatch.setattr(Intake, "config", lambda self: DEFAULT_CONFIG)
    calls = _sweeps(monkeypatch, GONE)
    with pytest.raises(TakeoverRefused, match="LEGACY_ATTEMPT_UNKNOWN"):
        Intake(state).supersede("task-n")
    assert not calls


def test_the_migration_runs_once(tmp_path):
    path, _ = _legacy_ledger(tmp_path)
    ledger = OwnershipLedger(path)
    task_id = ledger.bind("k9", {"a": 9}, OWNER, "card-k9").record.task_id
    ledger.set_status(task_id, "FAILED")  # after the upgrade, with no fence
    assert OwnershipLedger(path).unswept_attempt(task_id) is None
    assert OwnershipLedger(tmp_path / "fresh.sqlite").unswept_attempt(task_id) is None


def test_intake_supersede_gives_the_takeover_the_journal_and_the_account(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    path, _ = _legacy_ledger(tmp_path)
    path.rename(state / "ownership.sqlite")
    (tmp_path / "journal.sqlite").rename(state / "journal.sqlite")
    intake = Intake(state)
    monkeypatch.setattr(
        Intake, "config", lambda self: {**DEFAULT_CONFIG, "agy_run_as_user": "cw-worker"}
    )
    calls = _sweeps(monkeypatch, GONE)
    assert intake.supersede("task-f") == {"task_id": "task-f", "status": "SUPERSEDED"}
    assert calls == [(ATTEMPT, "cw-worker")] * 2  # clean twice in a row


def _recover_with(tmp_path, monkeypatch, started, configured, answer):
    ledger, task_id = _dispatched(tmp_path)
    journal = Journal(tmp_path / "journal.sqlite")
    journal.record(task_id, ATTEMPT, "DISPATCHED", str(tmp_path / "runs"))
    journal.record(task_id, ATTEMPT, "WORKER_STARTED", started)
    seen = []

    def sweep(var, value, **kw):
        seen.append(kw.get("account"))
        return answer

    monkeypatch.setattr(recovery, "sweep_marked", sweep)
    monkeypatch.setattr("cairnwork.confined.CONFIRM_WAIT", 0)
    ledger_, notary, journal_ = stores(tmp_path)
    (decision,) = recovery.recover(
        ledger=ledger_, notary=notary, journal=journal_, run_root=tmp_path / "runs",
        worker_account=configured,
    )  # fmt: skip
    return ledger, task_id, decision, set(seen)


def test_recovery_sweeps_the_worker_account_the_journal_names(tmp_path, monkeypatch):
    answer = Sweep(True, account=1)
    ledger, task_id, decision, seen = _recover_with(
        tmp_path, monkeypatch, "run_as=cw-worker", None, answer
    )
    assert decision.new_status == "FAILED" and decision.sweep_unavailable, decision.reason
    assert decision.detail.startswith("WORKER_ACCOUNT_BUSY") and seen == {"cw-worker"}
    assert ledger.unswept_attempt(task_id) == Mark(ATTEMPT, "cw-worker", "")


def test_recovery_assumes_the_configured_account_for_a_worker_journaled_before_244(
    tmp_path, monkeypatch
):
    """Codex #244 round 2 #4: an old WORKER_STARTED names no account; it is not the owner's."""
    ledger, task_id, decision, seen = _recover_with(
        tmp_path, monkeypatch, "", "cw-worker", Sweep(True, account=1)
    )
    assert seen == {"cw-worker"} and decision.sweep_unavailable
    assert ledger.unswept_attempt(task_id) == Mark(ATTEMPT, "cw-worker", "")


def test_recovery_fences_a_worker_of_unknown_account_until_one_is_configured(tmp_path, monkeypatch):
    ledger, task_id, decision, seen = _recover_with(tmp_path, monkeypatch, "", None, GONE)
    assert seen == {None}  # the owner's processes are still swept and killed
    assert decision.sweep_unavailable and decision.detail.startswith("SWEEP_UNAVAILABLE")
    assert ledger.unswept_attempt(task_id) == Mark(ATTEMPT, unswept.UNKNOWN_ACCOUNT, "")
    calls = _sweeps(monkeypatch, GONE)
    with pytest.raises(TakeoverRefused, match="agy_run_as_user is not set"):
        takeover.supersede(ledger, task_id)
    assert not calls
    assert takeover.supersede(ledger, task_id, account="cw-worker").status == "SUPERSEDED"
    assert calls == [(ATTEMPT, "cw-worker")] * 2


def test_an_old_worker_started_entry_names_no_account():
    from cairnwork.journal import Entry

    def started(detail):
        return [Entry("t", "a", "WORKER_STARTED", detail)]

    assert unswept.journaled_account(started("run_as=cw-worker")) == "cw-worker"
    assert unswept.journaled_account(started("run_as=")) == ""
    assert unswept.journaled_account(started("")) == unswept.UNKNOWN_ACCOUNT  # before #244
    assert unswept.journaled_account([]) is None


class _MayLive(EditWorker):
    run_as = "cw-worker"

    def run(self, workdir, task):
        raise WorkerMayLive("WORKER_SWEEP_UNCLEAN: x", account="cw-worker")


def _go(project, tmp_path, worker):  # noqa: F811
    ledger, notary, journal = stores(tmp_path)
    return run_task(
        task(project), ledger=ledger, notary=notary, journal=journal, worker=worker,
        run_root=tmp_path / "runs",
    )  # fmt: skip


@pytest.mark.sandbox
def test_a_live_run_whose_worker_may_live_is_fenced(project, tmp_path):  # noqa: F811
    """#244 review: WORKER_SWEEP_UNCLEAN used to end FAILED with no mark."""
    result = _go(project, tmp_path, _MayLive({}))
    ledger, _, journal = stores(tmp_path)
    assert result.status == "FAILED" and result.reason.startswith("WORKER_FAILED")
    assert ledger.unswept_attempt(result.task_id) == Mark(result.attempt_id, "cw-worker", "")
    started = [e for e in journal.entries(result.task_id) if e.phase == "WORKER_STARTED"]
    assert [e.detail for e in started] == ["run_as=cw-worker"]  # for recovery after a crash


@pytest.mark.sandbox
def test_a_failed_mark_write_keeps_the_live_run_in_flight(project, tmp_path, monkeypatch):  # noqa: F811
    _fail_marks(monkeypatch, _MarkFails)
    with pytest.raises(_MarkFails):
        _go(project, tmp_path, _MayLive({}))
    ledger = stores(tmp_path)[0]
    (task,) = ledger.in_flight()
    assert task.status == "DISPATCHED" and ledger.unswept_attempt(task.task_id) is None


@pytest.mark.sandbox
def test_an_ordinary_worker_failure_is_not_fenced(project, tmp_path):  # noqa: F811
    class Fails(EditWorker):
        def run(self, workdir, task):
            raise RuntimeError("model unavailable")

    result = _go(project, tmp_path, Fails({}))
    ledger, _, journal = stores(tmp_path)
    assert result.status == "FAILED" and ledger.unswept_attempt(result.task_id) is None
    started = [e for e in journal.entries(result.task_id) if e.phase == "WORKER_STARTED"]
    assert [e.detail for e in started] == ["run_as="]  # the owner's uid, named


def test_recovery_drops_the_legacy_mark_of_a_task_that_never_left_bound(tmp_path):
    """#244 review NIT: under the exclusive run lock a task still BOUND started nothing (the
    ledger moves to DISPATCHED before anything is spawned), so its LEGACY mark goes."""
    path, journal = _legacy_ledger(tmp_path)
    ledger = OwnershipLedger(path)
    decisions = recovery.recover(
        ledger=ledger, notary=stores(tmp_path)[1], journal=journal, run_root=tmp_path / "runs"
    )
    by_task = {d.task_id: d for d in decisions}
    assert by_task["task-b"].reason == "INTERRUPTED_BEFORE_DISPATCH"
    assert ledger.get(task_id="task-b").status == "FAILED"
    assert ledger.unswept_attempt("task-b") is None
    assert ledger.unswept_attempt("task-d") == Mark(unswept.LEGACY, "", "")  # it may have run
    assert ledger.unswept_attempt("task-n") == Mark(unswept.LEGACY, "", "")
