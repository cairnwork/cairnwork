"""Recovery acts only inside the state's own run root, for an attempt id a run makes.

The journal is a file. A run root its DISPATCHED row names that is not the state's is neither
cleaned nor searched for processes (only re-scanned for names and reported), and an attempt id
that is not ``a`` + 16 hex names no directory. The sweep is faked: no test signals a process,
and every directory lives under tmp_path.
"""

import os

import pytest

import cairnwork.confined as confined
import cairnwork.recovery as recovery
from cairnwork.confined import Sweep
from cairnwork.journal import Journal
from cairnwork.notary import Notary
from cairnwork.ownership import OWNER, OwnershipLedger

ATTEMPT = "a0123456789abcdef"


@pytest.fixture
def swept(monkeypatch):
    """The sweep faked: records the cwd_dirs recovery passes, kills nothing."""
    seen: list[tuple[str, ...]] = []

    def sweep(var, value, **kw):
        seen.append(tuple(kw.get("cwd_dirs") or ()))
        return Sweep(listed=True)

    monkeypatch.setattr(recovery, "sweep_marked", sweep)
    monkeypatch.setattr(confined, "CONFIRM_WAIT", 0)
    return seen


def _recover(tmp_path, attempt, journaled_root):
    state, run_root = tmp_path / "state", tmp_path / "runs"
    state.mkdir()
    ledger = OwnershipLedger(state / "own.sqlite")
    task = ledger.bind("k-roots", {"a": 1}, OWNER, "card-roots").record.task_id
    ledger.set_status(task, "DISPATCHED")
    journal = Journal(state / "journal.sqlite")
    journal.record(task, attempt, "DISPATCHED", str(journaled_root))
    notary = Notary(state / "notary.sqlite")
    (decision,) = recovery.recover(ledger=ledger, notary=notary, journal=journal, run_root=run_root)
    return decision


def _attempt_dirs(root, attempt):
    made = [root / attempt, root / f"{attempt}-q-v0.work", root / f"{attempt}-r-v0"]
    for path in made:
        (path / "sub").mkdir(parents=True)
        (path / "sub" / "keep.txt").write_text("x")
    return made


def test_a_journal_root_outside_the_run_root_is_neither_cleaned_nor_searched(tmp_path, swept):
    (tmp_path / "runs").mkdir()
    outside = tmp_path / "outside"
    theirs = _attempt_dirs(outside, ATTEMPT)
    decision = _recover(tmp_path, ATTEMPT, outside)
    assert all((path / "sub" / "keep.txt").exists() for path in theirs)
    real_outside = os.path.realpath(outside)
    assert swept and not any(d.startswith(real_outside) for dirs in swept for d in dirs)
    assert "JOURNAL_RUN_ROOT_NOT_USED" in decision.detail
    assert decision.leftover_run_dirs == 3  # reported by name, left for the operator
    assert decision.new_status == "FAILED"


def test_the_state_run_root_is_still_cleaned_when_the_journal_names_another(tmp_path, swept):
    ours = _attempt_dirs(tmp_path / "runs", ATTEMPT)
    (tmp_path / "outside").mkdir()
    decision = _recover(tmp_path, ATTEMPT, tmp_path / "outside")
    assert not any(path.exists() for path in ours)
    assert {os.path.realpath(p) for p in ours[:2]} <= set(swept[0])  # searched before removal
    assert decision.leftover_run_dirs == 0


def test_the_run_root_spelled_through_a_symlink_is_the_same_root(tmp_path, swept):
    ours = _attempt_dirs(tmp_path / "runs", ATTEMPT)
    link = tmp_path / "runs-link"
    link.symlink_to(tmp_path / "runs")
    decision = _recover(tmp_path, ATTEMPT, link)
    assert not any(path.exists() for path in ours)
    assert decision.detail == "" and decision.leftover_run_dirs == 0


@pytest.mark.parametrize("attempt", ["keep", "a1", f"{ATTEMPT}-q-v0"])
def test_an_attempt_id_a_run_does_not_make_names_no_directory(tmp_path, swept, attempt):
    runs = tmp_path / "runs"
    theirs = _attempt_dirs(runs, attempt)
    decision = _recover(tmp_path, attempt, runs)
    assert all((path / "sub" / "keep.txt").exists() for path in theirs)
    assert swept and all(dirs == () for dirs in swept)
    assert "ATTEMPT_ID_NOT_A_RUN_ID" in decision.detail
