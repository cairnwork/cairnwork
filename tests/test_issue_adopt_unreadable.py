"""#443: a FAILED row whose stored request cannot be read ends in needs-fix once, never adopted."""
# ruff: noqa: F811

import errno
import json
import sqlite3
from types import SimpleNamespace

import pytest
from test_issue_runner import FakeGitHub, issue, setup  # noqa: F401

import cairnwork.issue_runner as runner
import cairnwork.issue_task as issue_task
from cairnwork.intake import Intake
from cairnwork.issue_task import IssueRefused

REPO = "James3014/demo"
CODE = "TASK_BOUND_REQUEST_UNREADABLE"


def _store_fails(monkeypatch, mode: str) -> dict[str, bool]:
    """Make Intake._store_request fail while ``toggle["fail"]``: write nothing ("none"), half the
    JSON ("partial"), the whole request ("whole") or a valid JSON the schema refuses ("schema")."""
    toggle, real = {"fail": True}, Intake._store_request

    def store(self, task_id, payload):
        if not toggle["fail"]:
            return real(self, task_id, payload)
        path = self.state / "requests" / f"{task_id}.json"
        text = json.dumps(payload, sort_keys=True)
        written = {"none": None, "partial": text[: len(text) // 2], "whole": text, "schema": "{}"}
        if written[mode] is not None:
            path.write_text(written[mode])
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(Intake, "_store_request", store)
    return toggle


def _failed_pass(setup, monkeypatch, mode: str) -> tuple[FakeGitHub, str]:
    """Pass 1: take() binds the row, the store fails, the row is FAILED; the store heals."""
    toggle = _store_fails(monkeypatch, mode)
    github = FakeGitHub([issue()])
    first = runner.take(setup["intake"], github, setup["clone"])
    assert first[0]["error"].startswith("OSError") and github.labels[7] == {runner.QUEUE}
    toggle["fail"] = False
    (row,) = _rows(setup)
    assert row[1] == "FAILED"
    return github, row[0]


def _rows(setup) -> list[tuple[str, str]]:
    with sqlite3.connect(setup["intake"].ledger.path) as conn:
        return conn.execute("SELECT task_id, status FROM task_ownership").fetchall()


@pytest.mark.parametrize("mode", ["none", "partial"])
def test_a_failed_row_with_an_unreadable_request_ends_in_needs_fix_once(setup, monkeypatch, mode):
    github, task_id = _failed_pass(setup, monkeypatch, mode)
    out = runner.take(setup["intake"], github, setup["clone"])
    assert out == [{"issue": 7, "refused": f"{CODE}: {task_id}"}]
    assert github.labels[7] == {runner.NEEDS_FIX}
    assert len(github.comments) == 1 and CODE in github.comments[-1][1]
    for leak in ("requests", "Errno", "No space", "Expecting", task_id):
        assert leak not in github.comments[-1][1]
    # never adopted, never resubmitted: no record, nothing started, the one row still FAILED
    assert not runner._record_path(setup["intake"], REPO, 7).exists()
    assert len(setup["started"]) == 0 and _rows(setup) == [(task_id, "FAILED")]
    assert runner.settle(setup["intake"], github, setup["clone"]) == [] and github.prs == []
    assert runner.take(setup["intake"], github, setup["clone"]) == []  # out of the queue
    assert len(github.comments) == 1


@pytest.mark.parametrize("status", ["BOUND", "DISPATCHED"])
def test_a_live_row_with_an_unreadable_request_still_errors(setup, monkeypatch, status):
    """Not FAILED: recovery owns the row; the runner keeps failing closed and leaves it queued."""
    github, task_id = _failed_pass(setup, monkeypatch, "none")
    with sqlite3.connect(setup["intake"].ledger.path) as conn:
        conn.execute("UPDATE task_ownership SET status = ? WHERE task_id = ?", (status, task_id))
    out = runner.take(setup["intake"], github, setup["clone"])
    assert out[0]["error"].startswith("FileNotFoundError") and "refused" not in out[0]
    assert github.labels[7] == {runner.QUEUE} and github.comments == []
    assert not runner._record_path(setup["intake"], REPO, 7).exists()
    assert len(setup["started"]) == 0 and _rows(setup) == [(task_id, status)]


def test_a_failed_row_with_a_readable_other_request_still_differs(setup, monkeypatch):
    github, task_id = _failed_pass(setup, monkeypatch, "whole")
    github.bodies[7] = {**github.bodies[7], "title": "fix x (edited title)"}
    out = runner.take(setup["intake"], github, setup["clone"])
    refused = out[0]["refused"]  # the instructions quote the title, so both differ
    assert refused.startswith(f"TASK_BOUND_REQUEST_DIFFERS: {task_id}: title")
    assert github.labels[7] == {runner.NEEDS_FIX}


def test_a_failed_row_with_the_same_readable_request_is_still_adopted(setup, monkeypatch):
    github, task_id = _failed_pass(setup, monkeypatch, "whole")
    out = runner.take(setup["intake"], github, setup["clone"])
    assert out == [{"issue": 7, "task_id": task_id, "already": True}]
    assert len(setup["started"]) == 0 and _rows(setup) == [(task_id, "FAILED")]
    assert runner.settle(setup["intake"], github, setup["clone"]) == [
        {"issue": 7, "status": "FAILED"}
    ]


def test_a_stored_request_the_schema_refuses_is_still_bad_request(setup, monkeypatch):
    github, _ = _failed_pass(setup, monkeypatch, "schema")
    out = runner.take(setup["intake"], github, setup["clone"])
    assert out[0]["refused"].startswith("BAD_REQUEST")
    assert github.labels[7] == {runner.NEEDS_FIX} and "BAD_REQUEST" in github.comments[-1][1]


def test_a_failed_relabel_leaves_the_issue_queued_and_unadopted(setup, monkeypatch):
    github, task_id = _failed_pass(setup, monkeypatch, "none")

    def relabel(number, add, remove):
        raise RuntimeError("gh api failed: HTTP 502")

    monkeypatch.setattr(github, "relabel", relabel)
    out = runner.take(setup["intake"], github, setup["clone"])
    error = "RuntimeError: gh api failed: HTTP 502"
    assert out == [{"issue": 7, "refused": f"{CODE}: {task_id}", "error": error}]
    assert github.labels[7] == {runner.QUEUE} and github.comments == []
    assert not runner._record_path(setup["intake"], REPO, 7).exists()
    assert len(setup["started"]) == 0 and _rows(setup) == [(task_id, "FAILED")]


def test_adoptable_turns_an_unreadable_request_into_a_refusal_only_for_a_failed_row():
    want = {"card_hash": "gh:r#1:abc"}

    def unreadable(task_id):
        raise FileNotFoundError(f"/state/requests/{task_id}.json")

    def row(status):
        return SimpleNamespace(card_hash="gh:r#1:abc", task_id="task-1", status=status)

    with pytest.raises(IssueRefused) as refused:
        issue_task.adoptable(row("FAILED"), unreadable, want)
    assert refused.value.code == CODE and str(refused.value) == f"{CODE}: task-1"
    for status in ("BOUND", "DISPATCHED", "COMPLETED"):
        with pytest.raises(FileNotFoundError):
            issue_task.adoptable(row(status), unreadable, want)
    other = SimpleNamespace(card_hash="gh:r#1:abd", task_id="task-1", status="FAILED")
    assert issue_task.adoptable(other, unreadable, want) is False  # another block: card first
