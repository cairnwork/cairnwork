"""relabel adds the new label before it removes the old one (#634).

One `gh issue edit --add-label X --remove-label Y` is two operations on GitHub; if only the removal
lands, the Issue has none of the runner's labels and no pass lists it again. Two calls, add
first, leave at worst both labels, which the runner already settles."""
# ruff: noqa: F811

import pytest
from test_issue_runner import FakeGitHub, issue
from test_issue_runner import setup as setup  # noqa: F401 - fixture
from test_issue_settle_crash import (
    BRANCH,
    PR,
    CrashingGitHub,
    _completed_round,
    _hook,
    _hook_runs,
    _origin_branches,
)

import cairnwork.issue_hook as issue_hook
import cairnwork.issue_runner as runner
from cairnwork.issue_github import GitHub


class _Recorder(GitHub):
    def __init__(self, fail_on: int | None = None) -> None:
        super().__init__("/nonexistent/gh", "James3014/demo")
        self.calls: list[tuple[str, ...]] = []
        self.fail_on = fail_on

    def _run(self, *args: str, stdin: str | None = None) -> str:
        if len(self.calls) == self.fail_on:
            raise RuntimeError("gh issue failed: network")
        self.calls.append(args)
        return ""


def test_the_new_label_is_added_before_the_old_one_is_removed():
    github = _Recorder()
    github.relabel(7, "cairnwork:running", "cairnwork:queue")
    assert github.calls == [
        ("issue", "edit", "7", "-R", "James3014/demo", "--add-label", "cairnwork:running"),
        ("issue", "edit", "7", "-R", "James3014/demo", "--remove-label", "cairnwork:queue"),
    ]


def test_a_failed_add_removes_nothing():
    github = _Recorder(fail_on=0)
    with pytest.raises(RuntimeError):
        github.relabel(7, "cairnwork:running", "cairnwork:queue")
    assert github.calls == []


# --- the state a stopped relabel now leaves (both labels) converges --------------------------


def test_take_with_both_labels_is_adopted_once(setup, monkeypatch):
    """queue->running stops after the add: queue and running. The next take adopts the task
    (no second dispatch) and finishes the relabel."""
    github = FakeGitHub([issue()])
    sound = github.relabel
    calls = {"n": 0}

    def add_only_once(number, add, remove):
        calls["n"] += 1
        if calls["n"] == 1:
            github.labels[number] = github.labels[number] | {add}
            raise RuntimeError("gh issue failed: could not remove label")
        sound(number, add, remove)

    github.relabel = add_only_once
    out = runner.take(setup["intake"], github, setup["clone"])[0]
    assert "could not remove" in out["error"]
    assert github.labels[7] == {runner.QUEUE, runner.RUNNING}
    again = runner.take(setup["intake"], github, setup["clone"])[0]
    assert again["already"] is True and github.labels[7] == {runner.RUNNING}
    assert len(setup["started"]) == 1


def test_done_with_both_labels_converges(setup, monkeypatch, tmp_path):
    """running->done stops after the add: running and done. The next pass settles again (the PR
    is reused) and posts the done comment and runs the hook once each."""

    class AddOnly(CrashingGitHub):
        armed = True

        def relabel(self, number, add, remove):
            if add == runner.DONE and self.armed:
                self.armed = False
                self.labels[number] = self.labels[number] | {add}
                raise RuntimeError("gh issue failed: could not remove label")
            super().relabel(number, add, remove)

    github = AddOnly([issue()])
    taken, commit = _completed_round(setup, monkeypatch, github, hook=_hook(tmp_path))
    out = runner.settle(setup["intake"], github, setup["clone"])
    assert "could not remove" in out[0]["error"] and _hook_runs(setup) == []
    runner.settle(setup["intake"], github, setup["clone"])
    runner.settle(setup["intake"], github, setup["clone"])
    assert [pr[0] for pr in github.prs] == [BRANCH]
    assert _origin_branches(setup) == {f"refs/heads/{BRANCH}": commit}
    assert github.labels[7] == {runner.DONE}
    assert [n for n, text in github.comments if PR in text] == [7]
    assert _hook_runs(setup) == [taken["task_id"]]
    assert not list((setup["intake"].state / "issues").glob(f"*{issue_hook.OWED}"))
