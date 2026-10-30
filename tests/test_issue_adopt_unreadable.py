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


def _legacy_failed(setup, mode: str) -> tuple[FakeGitHub, str]:
    """A row the old store order left behind (bound, then the store failed, then FAILED; #443,
    #466): built from a normal pass by setting the row FAILED, dropping the runner's record and
    damaging the stored request: "none" (absent), "empty", "partial" (half the JSON), "whole"
    (untouched) or "schema" (valid JSON the schema refuses)."""
    github = FakeGitHub([issue()])
    (taken,) = runner.take(setup["intake"], github, setup["clone"])
    task_id = taken["task_id"]
    setup["intake"].ledger.set_status(task_id, "FAILED")
    runner._record_path(setup["intake"], REPO, 7).unlink()
    github.labels[7], github.comments[:] = {runner.QUEUE}, []
    path = setup["intake"].state / "requests" / f"{task_id}.json"
    text = path.read_text()
    if mode == "none":
        path.unlink()
    elif mode != "whole":
        path.write_text({"empty": "", "partial": text[: len(text) // 2], "schema": "{}"}[mode])
    assert _rows(setup) == [(task_id, "FAILED")] and len(setup["started"]) == 1
    return github, task_id


def _rows(setup) -> list[tuple[str, str]]:
    with sqlite3.connect(setup["intake"].ledger.path) as conn:
        return conn.execute("SELECT task_id, status FROM task_ownership").fetchall()


@pytest.mark.parametrize("mode", ["none", "empty", "partial"])
def test_a_failed_row_with_an_unreadable_request_ends_in_needs_fix_once(setup, mode):
    github, task_id = _legacy_failed(setup, mode)
    out = runner.take(setup["intake"], github, setup["clone"])
    assert out == [{"issue": 7, "refused": f"{CODE}: {task_id}"}]
    assert github.labels[7] == {runner.NEEDS_FIX}
    (text,) = [text for _, text in github.comments]
    assert CODE in text and "supersede" in text and "修正 `eia-task` 區塊" not in text
    for leak in ("requests", "Errno", "Expecting", "codec", task_id):
        assert leak not in text
    # never adopted, never resubmitted: no record, nothing started, the one row still FAILED
    assert not runner._record_path(setup["intake"], REPO, 7).exists()
    assert len(setup["started"]) == 1 and _rows(setup) == [(task_id, "FAILED")]
    assert runner.settle(setup["intake"], github, setup["clone"]) == [] and github.prs == []
    assert runner.take(setup["intake"], github, setup["clone"]) == []  # out of the queue
    assert len(github.comments) == 1


@pytest.mark.parametrize("status", ["BOUND", "DISPATCHED"])
def test_a_live_row_with_an_unreadable_request_still_errors(setup, status):
    """Not FAILED: recovery owns the row; the runner keeps failing closed and leaves it queued."""
    github, task_id = _legacy_failed(setup, "none")
    with sqlite3.connect(setup["intake"].ledger.path) as conn:
        conn.execute("UPDATE task_ownership SET status = ? WHERE task_id = ?", (status, task_id))
    out = runner.take(setup["intake"], github, setup["clone"])
    _still_queued(setup, github, task_id, out, "RequestUnreadable", status)


def _still_queued(setup, github, task_id, out, error, status="FAILED") -> None:
    assert out[0]["error"].startswith(error) and "refused" not in out[0]
    assert github.labels[7] == {runner.QUEUE} and github.comments == []
    assert not runner._record_path(setup["intake"], REPO, 7).exists()
    assert len(setup["started"]) == 1 and _rows(setup) == [(task_id, status)]


@pytest.mark.parametrize(
    "code", [errno.ENOENT, errno.EIO, errno.EACCES, errno.EMFILE], ids=errno.errorcode.get
)
def test_a_config_read_failure_inside_load_request_still_errors(setup, monkeypatch, code):
    """Only the request file itself turns into needs-fix; config.json going away (between exists()
    and the read) or failing to read is Cairnwork's state: the Issue stays queued."""
    github, task_id = _legacy_failed(setup, "whole")
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


@pytest.mark.parametrize("code", [errno.EACCES, errno.EIO], ids=errno.errorcode.get)
def test_a_request_file_that_cannot_be_opened_still_errors(setup, monkeypatch, code):
    """EACCES or EIO on the request file is not "missing or cut short": still queued."""
    github, task_id = _legacy_failed(setup, "whole")
    real = os.open

    def opener(path, flags, *args, **kwargs):
        if os.fspath(path) == f"{task_id}.json":
            raise OSError(code, os.strerror(code), os.fspath(path))
        return real(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", opener)
    out = runner.take(setup["intake"], github, setup["clone"])
    _still_queued(setup, github, task_id, out, type(OSError(code, "x")).__name__)


def _no_requests_dir(requests: Path, task_id: str) -> None:
    shutil.rmtree(requests)


def _requests_dir_symlink(requests: Path, task_id: str) -> None:
    requests.rename(requests.with_name("requests-real"))
    requests.symlink_to(requests.with_name("requests-real"))


def _dangling_symlink(requests: Path, task_id: str) -> None:
    (requests / f"{task_id}.json").unlink()
    (requests / f"{task_id}.json").symlink_to(requests / "nowhere.json")


def _symlink_to(content: bytes):
    def damage(requests: Path, task_id: str) -> None:
        path = requests / f"{task_id}.json"
        target = requests.parent / "elsewhere.json"
        target.write_bytes(content if content else path.read_bytes())  # b"": a whole copy
        path.unlink()
        path.symlink_to(target)

    damage.__name__ = f"_symlink_to_{'copy' if not content else 'garbage'}"
    return damage


def _not_ascii(requests: Path, task_id: str) -> None:
    path = requests / f"{task_id}.json"
    data = bytearray(path.read_bytes())
    data[10] = 0xFF  # json.dumps writes ASCII only: this byte is damage, not a cut-short store
    path.write_bytes(bytes(data))


def _malformed(requests: Path, task_id: str) -> None:
    path = requests / f"{task_id}.json"
    path.write_text(path.read_text().replace('": "', '": ?', 1))  # complete, malformed: not a cut


def _cut_utf8(requests: Path, task_id: str) -> None:
    path = requests / f"{task_id}.json"
    path.write_bytes(path.read_bytes()[:40] + b"\xe4")


@pytest.mark.parametrize(
    ("damage", "error"),
    [
        (_no_requests_dir, "FileNotFoundError"),
        (_requests_dir_symlink, ""),  # ELOOP or ENOTDIR, by platform
        (_dangling_symlink, "OSError"),  # ELOOP: O_NOFOLLOW
        (_symlink_to(b"not json"), "OSError"),
        (_symlink_to(b""), "OSError"),
        (_not_ascii, "UnicodeDecodeError"),
        (_cut_utf8, "UnicodeDecodeError"),
        (_malformed, "JSONDecodeError"),
    ],
    ids=lambda v: getattr(v, "__name__", v) or "any",
)
def test_a_request_in_a_damaged_state_still_errors(setup, damage, error):
    """Only a request file absent from the held requests/ directory, or ASCII JSON cut short, is the
    request's own failure (#466): a missing or symlinked requests/, a symlink at the path (followed
    or not), or bytes the intake never writes are damaged state, so the Issue stays queued."""
    github, task_id = _legacy_failed(setup, "whole")
    damage(setup["intake"].state / "requests", task_id)
    out = runner.take(setup["intake"], github, setup["clone"])
    _still_queued(setup, github, task_id, out, error)


def test_requests_recreated_between_the_read_and_a_later_look_still_errors(setup, monkeypatch):
    """#450: requests/ gone at the read and back (empty) right after: the read's own failure says
    the directory was missing, so the Issue stays queued; no second look makes it needs-fix."""
    github, task_id = _legacy_failed(setup, "whole")
    requests, real = setup["intake"].state / "requests", Intake.load_request

    def load(self, task_id):
        shutil.rmtree(requests)
        try:
            return real(self, task_id)
        finally:
            requests.mkdir(mode=0o700)

    monkeypatch.setattr(Intake, "load_request", load)
    out = runner.take(setup["intake"], github, setup["clone"])
    _still_queued(setup, github, task_id, out, "FileNotFoundError")


def test_a_failed_row_with_a_readable_other_request_still_differs(setup):
    github, task_id = _legacy_failed(setup, "whole")
    github.bodies[7] = {**github.bodies[7], "title": "fix x (edited title)"}
    out = runner.take(setup["intake"], github, setup["clone"])
    refused = out[0]["refused"]  # the instructions quote the title, so both differ
    assert refused.startswith(f"TASK_BOUND_REQUEST_DIFFERS: {task_id}: title")
    assert github.labels[7] == {runner.NEEDS_FIX}
    assert "修正 `eia-task` 區塊" in github.comments[-1][1]  # other refusals keep their text


def test_a_failed_row_with_the_same_readable_request_is_still_adopted(setup):
    github, task_id = _legacy_failed(setup, "whole")
    out = runner.take(setup["intake"], github, setup["clone"])
    assert out == [{"issue": 7, "task_id": task_id, "already": True}]
    assert len(setup["started"]) == 1 and _rows(setup) == [(task_id, "FAILED")]
    assert runner.settle(setup["intake"], github, setup["clone"]) == [
        {"issue": 7, "status": "FAILED"}
    ]


def test_a_stored_request_the_schema_refuses_is_still_bad_request(setup):
    github, _ = _legacy_failed(setup, "schema")
    out = runner.take(setup["intake"], github, setup["clone"])
    assert out[0]["refused"].startswith("BAD_REQUEST")
    assert github.labels[7] == {runner.NEEDS_FIX} and "BAD_REQUEST" in github.comments[-1][1]


def test_a_failed_relabel_leaves_the_issue_queued_and_unadopted(setup, monkeypatch):
    github, task_id = _legacy_failed(setup, "none")

    def relabel(number, add, remove):
        raise RuntimeError("gh api failed: HTTP 502")

    monkeypatch.setattr(github, "relabel", relabel)
    out = runner.take(setup["intake"], github, setup["clone"])
    error = "RuntimeError: gh api failed: HTTP 502"
    assert out == [{"issue": 7, "refused": f"{CODE}: {task_id}", "error": error}]
    assert github.labels[7] == {runner.QUEUE} and github.comments == []
    assert not runner._record_path(setup["intake"], REPO, 7).exists()
    assert len(setup["started"]) == 1 and _rows(setup) == [(task_id, "FAILED")]


def test_adoptable_turns_only_request_unreadable_into_a_refusal_and_only_for_failed():
    from cairnwork.issue_task import RequestUnreadable

    want = {"card_hash": "gh:r#1:abc"}

    def unreadable(task_id):
        raise RequestUnreadable(f"{task_id}: missing")

    def row(status):
        return SimpleNamespace(card_hash="gh:r#1:abc", task_id="task-1", status=status)

    with pytest.raises(IssueRefused) as refused:
        issue_task.adoptable(row("FAILED"), unreadable, want)
    assert refused.value.code == CODE and str(refused.value) == f"{CODE}: task-1"
    for status in ("BOUND", "DISPATCHED", "COMPLETED", "SUPERSEDED", "CANCELLED"):
        with pytest.raises(RequestUnreadable):
            issue_task.adoptable(row(status), unreadable, want)
    for error in (  # what load_request did not classify: never a refusal, even for FAILED
        FileNotFoundError(errno.ENOENT, "No such file", "/state/requests/task-1.json"),
        json.JSONDecodeError("cut", "{", 1),
        UnicodeDecodeError("utf-8", b"\xe4", 0, 1, "cut"),
        PermissionError(errno.EACCES, "Permission denied", "task-1.json"),
    ):

        def failing(task_id, error=error):
            raise error

        with pytest.raises(type(error)):
            issue_task.adoptable(row("FAILED"), failing, want)
    other = SimpleNamespace(card_hash="gh:r#1:abd", task_id="task-1", status="FAILED")
    assert issue_task.adoptable(other, unreadable, want) is False  # another block: card first
