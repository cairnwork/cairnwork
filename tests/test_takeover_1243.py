"""Issue #1243: reject an unsupersedable legacy task before sweeping it."""

import subprocess
import sys

import pytest

from cairnwork import takeover, unswept
from cairnwork.confined import Sweep
from cairnwork.intake import Intake, IntakeRefused
from cairnwork.ownership import OWNER, OwnershipLedger


@pytest.mark.parametrize("status", ["DISPATCHED"])
def test_refused_legacy_supersede_does_not_kill_worker(tmp_path, monkeypatch, status):
    """Category: migration LEGACY fence on a still-running, dispatched task."""
    state = tmp_path / "state"
    state.mkdir()
    intake = Intake(state)
    ledger = intake.ledger
    task_id = ledger.bind("key", {"task": 1}, OWNER, "card").record.task_id
    ledger.set_status(task_id, status)
    ledger._on_conn(unswept.mark, task_id, unswept.LEGACY, "")
    monkeypatch.setattr(takeover, "_legacy_attempts", lambda *_: [("legacy-a", "")])
    worker = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    sweeps = []

    def kill_worker(*_args, **_kwargs):
        sweeps.append(True)
        worker.kill()
        worker.wait()
        return Sweep(listed=True)

    monkeypatch.setattr(takeover, "sweep_marked", kill_worker)
    try:
        with pytest.raises(IntakeRefused) as refused:
            intake.supersede(task_id)
        assert refused.value.code == "NOT_SUPERSEDABLE"
        assert worker.poll() is None
        assert ledger.get(task_id=task_id).status == "DISPATCHED"
        assert ledger.unswept_attempt(task_id) == unswept.Mark(unswept.LEGACY, "", "")
        assert not sweeps
    finally:
        if worker.poll() is None:
            worker.kill()
            worker.wait()


@pytest.mark.parametrize(("attempt", "account"), [("a1", "")])
def test_allowed_supersede_sweeps_before_setting_status(tmp_path, monkeypatch, attempt, account):
    """Category: FAILED task with a known attempt fence remains eligible for takeover."""
    ledger = OwnershipLedger(tmp_path / "ownership.sqlite")
    task_id = ledger.bind("key", {"task": 1}, OWNER, "card").record.task_id
    ledger.set_status(task_id, "DISPATCHED")
    ledger.set_status(task_id, "FAILED", fence=(attempt, account))
    calls = []

    def sweep(*_args, **_kwargs):
        calls.append(ledger.get(task_id=task_id).status)
        return Sweep(listed=True)

    monkeypatch.setattr(takeover, "sweep_marked", sweep)
    assert takeover.supersede(ledger, task_id).status == "SUPERSEDED"
    assert calls == ["FAILED", "FAILED"]
    assert ledger.unswept_attempt(task_id) is None


@pytest.mark.parametrize(
    ("status", "expected"), [("BOUND", None), ("DISPATCHED", None), ("FAILED", "")]
)
def test_status_reports_unswept_only_for_failed_tasks(tmp_path, status, expected):
    """Category: migrated LEGACY fences across pre-failure task statuses."""
    (tmp_path / "state").mkdir()
    intake = Intake(tmp_path / "state")
    task_id = intake.ledger.bind("key", {"task": 1}, OWNER, "card").record.task_id
    if status != "BOUND":
        intake.ledger.set_status(task_id, "DISPATCHED")
    if status == "FAILED":
        intake.ledger.set_status(task_id, "FAILED")
    intake.ledger._on_conn(unswept.mark, task_id, unswept.LEGACY, "")
    out = intake.status(task_id=task_id)["unswept"]
    if expected is None:
        assert out is None
    else:
        assert out == {"attempt_id": unswept.LEGACY, "last_refusal": expected}
