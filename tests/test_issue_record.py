"""The per-Issue record: written atomically, and a bad one never sticks the Issue (#404)."""
# ruff: noqa: F811

import json
import os
import sqlite3

import pytest
from conftest import commit_change, git
from test_issue_runner import BLOCK, FakeGitHub, issue, setup  # noqa: F401

import cairnwork.issue_runner as runner
from cairnwork.intake import Intake


def _record(setup, number=7):
    return runner._record_path(setup["intake"], "James3014/demo", number)


def _move_main(setup):
    commit_change(setup["clone"], {"other.txt": "main moved\n"})
    git(setup["clone"], "push", "-q", "origin", "HEAD:main")


def _truncate(path):
    text = path.read_text()
    path.write_text(text[: len(text) // 2])  # what a crash mid-write left before #404


def test_a_truncated_record_is_rebuilt_from_the_ledger_by_the_next_take(setup):
    github = FakeGitHub([issue()])
    first = runner.take(setup["intake"], github, setup["clone"])[0]
    _truncate(_record(setup))
    github.labels[7] = {runner.QUEUE}  # the crash came before the relabel
    _move_main(setup)  # a resubmit would carry the new base and be refused
    again = runner.take(setup["intake"], github, setup["clone"])
    assert again == [{"issue": 7, "task_id": first["task_id"], "already": True}]
    assert json.loads(_record(setup).read_text())["task_id"] == first["task_id"]
    assert github.labels[7] == {runner.RUNNING} and len(setup["started"]) == 1


@pytest.mark.parametrize("damage", [_truncate, lambda path: path.unlink()])
def test_a_lost_record_is_rebuilt_even_after_main_moved(setup, damage):
    github = FakeGitHub([issue()])
    first = runner.take(setup["intake"], github, setup["clone"])[0]
    damage(_record(setup))
    github.labels[7] = {runner.QUEUE}
    _move_main(setup)
    assert runner.take(setup["intake"], github, setup["clone"])[0]["task_id"] == first["task_id"]


def test_a_ledger_row_for_another_block_with_the_same_key_is_not_adopted(setup):
    github = FakeGitHub([issue()])
    runner.take(setup["intake"], github, setup["clone"])
    _truncate(_record(setup))
    with sqlite3.connect(setup["intake"].state / "ownership.sqlite") as db:
        db.execute("UPDATE task_ownership SET card_hash = card_hash || 'x'")
    github.labels[7] = {runner.QUEUE}
    out = runner.take(setup["intake"], github, setup["clone"])[0]
    assert "already" not in out and github.labels[7] == {runner.QUEUE}  # the normal path refused
    assert "KeyReusedWithDifferentRequest" in out["error"]


def test_a_truncated_record_with_a_changed_block_takes_the_new_block(setup):
    github = FakeGitHub([issue()])
    first = runner.take(setup["intake"], github, setup["clone"])[0]
    _truncate(_record(setup))
    changed = issue(block={**BLOCK, "allowed_files": ["src/calc.py", "src/more.py"]})
    github.bodies[7], github.labels[7] = changed, {runner.QUEUE}
    again = runner.take(setup["intake"], github, setup["clone"])[0]
    assert again["task_id"] != first["task_id"] and again["duplicate"] is False
    assert json.loads(_record(setup).read_text())["task_id"] == again["task_id"]


@pytest.mark.parametrize("text", ['{"repo": "James3014/de', "[]", '{"issue": 7}', "\udcff"])
def test_settle_names_an_unreadable_record_and_settles_the_others(setup, monkeypatch, text):
    github = FakeGitHub([issue(7), issue(8)])
    runner.take(setup["intake"], github, setup["clone"])
    _record(setup, 7).write_bytes(text.encode("utf-8", "surrogateescape"))
    monkeypatch.setattr(Intake, "status", lambda self, **kw: {"status": "DISPATCHED"})
    out = runner.settle(setup["intake"], github, setup["clone"])
    assert out == [{"issue": 7, "error": out[0]["error"]}]  # 8 was looked at and left running
    assert "ISSUE_RECORD_UNREADABLE" in out[0]["error"]
    assert "JSONDecodeError" not in out[0]["error"]
    assert github.labels == {7: {runner.RUNNING}, 8: {runner.RUNNING}}


def test_a_crash_before_the_rename_leaves_the_old_record_and_no_temp_file(setup, monkeypatch):
    runner._remember(setup["intake"], "James3014/demo", 7, "task-old", "sha256:old")
    before = _record(setup).read_text()
    seen = []

    def crash(src, dst):
        seen.append(json.loads(open(src).read()))  # the new record was whole before the rename
        raise OSError("killed")

    monkeypatch.setattr(os, "replace", crash)
    with pytest.raises(OSError, match="killed"):
        runner._remember(setup["intake"], "James3014/demo", 7, "task-new", "sha256:new")
    assert seen[0]["task_id"] == "task-new"
    assert _record(setup).read_text() == before
    assert sorted(p.name for p in _record(setup).parent.iterdir()) == [_record(setup).name]


def test_the_record_is_private(setup):
    runner._remember(setup["intake"], "James3014/demo", 7, "task-1", "sha256:x")
    assert _record(setup).stat().st_mode & 0o077 == 0
