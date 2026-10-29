"""#443: a FAILED row whose stored request cannot be read ends in needs-fix once, never adopted."""
# ruff: noqa: F811

import errno
import json
import os
import shutil
import sqlite3
from pathlib import Path
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
    JSON ("partial"), cut UTF-8 ("utf8"), the whole request ("whole") or a valid JSON the schema
    refuses ("schema")."""
    toggle, real = {"fail": True}, Intake._store_request

    def store(self, task_id, payload):
        if not toggle["fail"]:
            return real(self, task_id, payload)
        path = self.state / "requests" / f"{task_id}.json"
        text = json.dumps(payload, sort_keys=True)
        written = {"none": None, "partial": text[: len(text) // 2], "whole": text, "schema": "{}"}
        if mode == "utf8":
            path.write_bytes(text.encode()[:40] + b"\xe4")
        elif written[mode] is not None:
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


@pytest.mark.parametrize("mode", ["none", "partial", "utf8"])
def test_a_failed_row_with_an_unreadable_request_ends_in_needs_fix_once(setup, monkeypatch, mode):
    github, task_id = _failed_pass(setup, monkeypatch, mode)
    out = runner.take(setup["intake"], github, setup["clone"])
    assert out == [{"issue": 7, "refused": f"{CODE}: {task_id}"}]
    assert github.labels[7] == {runner.NEEDS_FIX}
    assert len(github.comments) == 1 and CODE in github.comments[-1][1]
    for leak in ("requests", "Errno", "No space", "Expecting", "codec", task_id):
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


def _still_queued(setup, github, task_id, out, error: str) -> None:
    assert out[0]["error"].startswith(error) and "refused" not in out[0]
    assert github.labels[7] == {runner.QUEUE} and github.comments == []
    assert not runner._record_path(setup["intake"], REPO, 7).exists()
    assert len(setup["started"]) == 0 and _rows(setup) == [(task_id, "FAILED")]


@pytest.mark.parametrize(
    "code", [errno.ENOENT, errno.EIO, errno.EACCES, errno.EMFILE], ids=errno.errorcode.get
)
def test_a_config_read_failure_inside_load_request_still_errors(setup, monkeypatch, code):
    """Only the request file itself turns into needs-fix; config.json going away (between exists()
    and the read) or failing to read is Cairnwork's state: the Issue stays queued."""
    github, task_id = _failed_pass(setup, monkeypatch, "whole")
    config = setup["intake"].state / "config.json"
    inside, real_load, real_config = [False], Intake.load_request, Intake.config

    def load(self, task_id):
        inside[0] = True
        try:
            return real_load(self, task_id)
        finally:
            inside[0] = False

    def broken(self):
        if inside[0]:
            raise OSError(code, os.strerror(code), str(config))
        return real_config(self)

    monkeypatch.setattr(Intake, "load_request", load)
    monkeypatch.setattr(Intake, "config", broken)
    out = runner.take(setup["intake"], github, setup["clone"])
    _still_queued(setup, github, task_id, out, type(OSError(code, "x")).__name__)


def test_a_request_file_that_cannot_be_opened_still_errors(setup, monkeypatch):
    """EACCES (or EIO, EMFILE) on the request file is not "missing or cut short": still queued."""
    github, task_id = _failed_pass(setup, monkeypatch, "whole")
    real = Path.read_text

    def read_text(self, *args, **kwargs):
        if self.name == f"{task_id}.json":
            raise PermissionError(errno.EACCES, "Permission denied", str(self))
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)
    out = runner.take(setup["intake"], github, setup["clone"])
    _still_queued(setup, github, task_id, out, "PermissionError")


def _no_requests_dir(requests: Path, task_id: str) -> None:
    shutil.rmtree(requests)


def _dangling_symlink(requests: Path, task_id: str) -> None:
    (requests / f"{task_id}.json").symlink_to(requests / "nowhere.json")


def _requests_dir_symlink(requests: Path, task_id: str) -> None:
    requests.rename(requests.with_name("requests-real"))
    requests.symlink_to(requests.with_name("requests-real"))


@pytest.mark.parametrize("damage", [_no_requests_dir, _dangling_symlink, _requests_dir_symlink])
def test_a_missing_request_in_a_damaged_state_still_errors(setup, monkeypatch, damage):
    """Only a request file absent from a plain requests/ directory is "missing": a missing
    requests/, a dangling symlink at the path or a symlinked requests/ is damaged state (the
    intake creates the file with O_EXCL), so the Issue stays queued."""
    github, task_id = _failed_pass(setup, monkeypatch, "none")
    damage(setup["intake"].state / "requests", task_id)
    out = runner.take(setup["intake"], github, setup["clone"])
    _still_queued(setup, github, task_id, out, "FileNotFoundError")


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


def test_adoptable_turns_an_unreadable_request_into_a_refusal_only_for_a_failed_row(tmp_path):
    want = {"card_hash": "gh:r#1:abc"}
    (tmp_path / "requests").mkdir()

    def unreadable(task_id):
        path = tmp_path / "requests" / f"{task_id}.json"
        raise FileNotFoundError(errno.ENOENT, "No such file", str(path))

    def row(status):
        return SimpleNamespace(card_hash="gh:r#1:abc", task_id="task-1", status=status)

    with pytest.raises(IssueRefused) as refused:
        issue_task.adoptable(row("FAILED"), unreadable, want)
    assert refused.value.code == CODE and str(refused.value) == f"{CODE}: task-1"
    for status in ("BOUND", "DISPATCHED", "COMPLETED", "SUPERSEDED", "CANCELLED"):
        with pytest.raises(FileNotFoundError):
            issue_task.adoptable(row(status), unreadable, want)
    for error in (
        FileNotFoundError(errno.ENOENT, "No such file", str(tmp_path / "config.json")),
        FileNotFoundError(errno.ENOENT, "No such file", str(tmp_path / "requests/task-2.json")),
        FileNotFoundError(errno.ENOENT, "No such file", str(tmp_path / "other/task-1.json")),
        FileNotFoundError(errno.ENOENT, "No such file", "/no-such-state/requests/task-1.json"),
        FileNotFoundError("no file name"),
        PermissionError(errno.EACCES, "Permission denied", str(tmp_path / "requests/task-1.json")),
    ):

        def failing(task_id, error=error):
            raise error

        with pytest.raises(type(error)):
            issue_task.adoptable(row("FAILED"), failing, want)
    other = SimpleNamespace(card_hash="gh:r#1:abd", task_id="task-1", status="FAILED")
    assert issue_task.adoptable(other, unreadable, want) is False  # another block: card first


def test_only_a_missing_file_or_a_decode_error_counts_as_the_request_itself(tmp_path):
    (tmp_path / "requests").mkdir()
    request = str(tmp_path / "requests" / "task-1.json")
    assert issue_task._request_itself(FileNotFoundError(errno.ENOENT, "x", request), "task-1")
    assert issue_task._request_itself(json.JSONDecodeError("cut", "{", 1), "task-1")
    assert issue_task._request_itself(UnicodeDecodeError("utf-8", b"\xe4", 0, 1, "cut"), "task-1")
    for other in (ValueError("x"), PermissionError(errno.EACCES, "x", request), KeyError("x")):
        assert issue_task._request_itself(other, "task-1") is False
