"""#63 (c) and (d): the Issue runner's settle dies between two of its external effects.

A crash is a BaseException raised from the fake GitHub (settle catches only Exceptions, so it
ends the pass the way a process death would); the next pass then runs with a sound GitHub.
(c) push done, PR not opened (or opened, label not moved): the next pass pushes the same commit
to the same branch and leaves exactly one PR. (d) label moved to done, hook not run: the Issue
is no longer cairnwork:running, so settle never looks at it again; the hook is owed and a later
pass runs it once (issue_hook.py), and stays owed while an error skips it. Real local clone and
bare origin, no network."""

import json

import pytest
from conftest import commit_change, git
from issue_helpers import ATTEMPT, _completed
from test_issue_runner import FakeGitHub, _git_without_credentials, issue
from test_issue_runner import setup as setup  # noqa: F401 - fixture

import cairnwork.issue_hook as issue_hook
import cairnwork.issue_runner as runner
import cairnwork.post_verified as post_verified
from cairnwork.intake import Intake

BRANCH = f"cairnwork/7-{ATTEMPT}"
PR = "https://github.com/James3014/demo/pull/1"


class _Crash(BaseException):
    """Not an Exception: like a process death at that point of settle."""


class CrashingGitHub(FakeGitHub):
    """Dies once at `at`: "draft_pr" (after the push), "relabel" (PR open, label not moved),
    "relabeled" (label just moved to done), "comment" (done, no comment and no hook yet)."""

    def __init__(self, issues, at=""):
        super().__init__(issues)
        self.at = at

    def _die(self, point):
        if self.at == point:
            self.at = ""
            raise _Crash(point)

    def draft_pr(self, branch, title, body):
        self._die("draft_pr")
        return super().draft_pr(branch, title, body)

    def relabel(self, number, add, remove):
        if add == runner.DONE:
            self._die("relabel")
        super().relabel(number, add, remove)
        if add == runner.DONE:
            self._die("relabeled")

    def comment(self, number, text):
        if PR in text:
            self._die("comment")
        super().comment(number, text)


def _completed_round(setup, monkeypatch, github, hook=None):  # noqa: F811
    taken = runner.take(setup["intake"], github, setup["clone"])[0]
    commit = commit_change(setup["clone"], {"src/calc.py": "x = 2\n"})
    monkeypatch.setattr(Intake, "status", _completed(setup, taken, commit))
    monkeypatch.setattr(runner, "_git", _git_without_credentials)
    if hook is not None:
        (setup["intake"].state / "config.json").write_text(json.dumps({"post_verified_hook": hook}))
    return taken, commit


def _origin_branches(setup):  # noqa: F811
    refs = git(setup["origin"], "for-each-ref", "--format=%(refname) %(objectname)", "refs/heads/")
    return dict(line.split() for line in refs.splitlines() if "cairnwork/" in line)


@pytest.mark.parametrize("at", ["draft_pr", "relabel"])
def test_a_crash_between_push_and_pr_opens_exactly_one_pr(setup, monkeypatch, at):  # noqa: F811
    github = CrashingGitHub([issue()], at=at)
    _, commit = _completed_round(setup, monkeypatch, github)
    with pytest.raises(_Crash):
        runner.settle(setup["intake"], github, setup["clone"])
    assert _origin_branches(setup) == {f"refs/heads/{BRANCH}": commit}  # the push happened
    assert len(github.prs) == (0 if at == "draft_pr" else 1)
    assert github.labels[7] == {runner.RUNNING}
    out = runner.settle(setup["intake"], github, setup["clone"])
    assert out == [{"issue": 7, "pr": PR}]
    assert [pr[0] for pr in github.prs] == [BRANCH]  # one PR, on the attempt's branch
    assert _origin_branches(setup) == {f"refs/heads/{BRANCH}": commit}  # same commit, no other
    assert github.labels[7] == {runner.DONE}
    assert [n for n, text in github.comments if PR in text] == [7]
    assert runner.settle(setup["intake"], github, setup["clone"]) == []


def _hook(tmp_path):
    path = tmp_path / "hook.sh"
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(0o755)
    return str(path)


def _hook_runs(setup):  # noqa: F811
    log = setup["intake"].state / post_verified.LOG_NAME
    return (
        [json.loads(line)["task_id"] for line in log.read_text().splitlines()]
        if (log.exists())
        else []
    )


@pytest.mark.parametrize("at", ["relabeled", "comment"])
def test_a_crash_after_the_done_label_still_runs_the_hook_once(setup, monkeypatch, tmp_path, at):  # noqa: F811
    github = CrashingGitHub([issue()], at=at)
    taken, _ = _completed_round(setup, monkeypatch, github, hook=_hook(tmp_path))
    with pytest.raises(_Crash):
        runner.settle(setup["intake"], github, setup["clone"])
    assert github.labels[7] == {runner.DONE} and _hook_runs(setup) == []  # the window
    assert runner.settle(setup["intake"], github, setup["clone"]) == []  # result unchanged
    assert _hook_runs(setup) == [taken["task_id"]]  # owed, and run by the next pass
    assert runner.settle(setup["intake"], github, setup["clone"]) == []
    assert _hook_runs(setup) == [taken["task_id"]]  # once


def test_a_settled_hook_is_not_run_again(setup, monkeypatch, tmp_path):  # noqa: F811
    github = CrashingGitHub([issue()])
    taken, _ = _completed_round(setup, monkeypatch, github, hook=_hook(tmp_path))
    assert runner.settle(setup["intake"], github, setup["clone"]) == [{"issue": 7, "pr": PR}]
    assert runner.settle(setup["intake"], github, setup["clone"]) == []
    assert _hook_runs(setup) == [taken["task_id"]]


def test_an_owed_hook_waits_while_its_issue_is_still_running(setup, monkeypatch, tmp_path):  # noqa: F811
    """A GitHub error on the done label leaves the Issue running: the hook must not run before
    the labels are final (docs/ATTESTATION.md), in that pass or by the owed path."""
    github = CrashingGitHub([issue()])
    taken, _ = _completed_round(setup, monkeypatch, github, hook=_hook(tmp_path))
    sound = github.relabel

    def fails(number, add, remove):
        if add == runner.DONE:
            raise RuntimeError("gh: HTTP 502")
        sound(number, add, remove)

    github.relabel = fails
    out = runner.settle(setup["intake"], github, setup["clone"])
    assert "HTTP 502" in out[0]["error"] and _hook_runs(setup) == []
    github.relabel = sound
    assert runner.settle(setup["intake"], github, setup["clone"]) == [{"issue": 7, "pr": PR}]
    assert _hook_runs(setup) == [taken["task_id"]] and len(github.prs) == 1


def _owed(setup):  # noqa: F811
    return sorted(p.name for p in (setup["intake"].state / "issues").glob(f"*{issue_hook.OWED}"))


def _raises(*_args, **_kwargs):
    raise KeyError("post_verified_hook_seconds")


@pytest.mark.parametrize("why", ["hook-raised", "config-unreadable", "config-unopenable"])
def test_a_hook_skipped_by_an_error_stays_owed(setup, monkeypatch, tmp_path, why):  # noqa: F811
    """An error that skips the hook step (alerted, never a settle result) keeps the marker, so a
    later pass runs the hook; once it has run the marker goes."""
    github = CrashingGitHub([issue()])
    taken, _ = _completed_round(setup, monkeypatch, github, hook=_hook(tmp_path))
    config = setup["intake"].state / "config.json"
    sound = config.read_text()
    if why == "hook-raised":
        monkeypatch.setattr(issue_hook, "run_post_verified_hook", _raises)
    else:  # after the done comment, the last step before the hook: _open_pr reads it too
        comment = github.comment

        def then_break(number, text):
            comment(number, text)
            if why == "config-unreadable":
                config.write_text(sound[:-1])  # still names the hook, no longer parses
            else:  # cannot be read at all, so it may name one
                config.unlink()
                config.mkdir()

        github.comment = then_break
    assert runner.settle(setup["intake"], github, setup["clone"]) == [{"issue": 7, "pr": PR}]
    owed = [f"James3014__demo__7__{taken['task_id']}{issue_hook.OWED}"]
    assert _hook_runs(setup) == [] and _owed(setup) == owed
    assert runner.settle(setup["intake"], github, setup["clone"]) == []  # still skipped: kept
    assert _hook_runs(setup) == [] and _owed(setup) == owed
    monkeypatch.setattr(issue_hook, "run_post_verified_hook", post_verified.run_post_verified_hook)
    github.comment = CrashingGitHub.comment.__get__(github)
    if config.is_dir():
        config.rmdir()
    config.write_text(sound)
    assert runner.settle(setup["intake"], github, setup["clone"]) == []  # result unchanged
    assert _hook_runs(setup) == [taken["task_id"]] and _owed(setup) == []
    assert runner.settle(setup["intake"], github, setup["clone"]) == []
    assert _hook_runs(setup) == [taken["task_id"]]  # once


def _crashed_after_done(setup, monkeypatch, tmp_path):  # noqa: F811
    github = CrashingGitHub([issue()], at="relabeled")
    taken, _ = _completed_round(setup, monkeypatch, github, hook=_hook(tmp_path))
    with pytest.raises(_Crash):
        runner.settle(setup["intake"], github, setup["clone"])
    (marker,) = (setup["intake"].state / "issues").glob(f"*{issue_hook.OWED}")
    return github, taken, marker


def test_an_owed_hook_runs_only_for_a_task_still_core_verified(setup, monkeypatch, tmp_path):  # noqa: F811
    """The marker names a task; the next pass reads its status again from the state and runs the
    hook only for the Core-verified candidate _open_pr checked."""
    github, _, marker = _crashed_after_done(setup, monkeypatch, tmp_path)
    verified = Intake.status
    monkeypatch.setattr(Intake, "status", lambda s, **kw: {**verified(s, **kw), "status": "FAILED"})
    assert runner.settle(setup["intake"], github, setup["clone"]) == []
    assert _hook_runs(setup) == [] and marker.exists()


def test_an_owed_marker_of_another_repo_is_left_alone(setup, monkeypatch, tmp_path):  # noqa: F811
    """One state, one repo: a marker whose name only starts like this repo's (James3014/demo__x)
    is not this repo's to run."""
    github, taken, marker = _crashed_after_done(setup, monkeypatch, tmp_path)
    other = marker.with_name(f"James3014__demo__x__7__{taken['task_id']}{issue_hook.OWED}")
    other.write_text(
        json.dumps({"repo": "James3014/demo__x", "issue": 7, "task_id": taken["task_id"]})
    )
    marker.unlink()
    assert runner.settle(setup["intake"], github, setup["clone"]) == []
    assert _hook_runs(setup) == [] and other.exists()


def test_an_issue_run_again_keeps_what_an_earlier_task_owes(setup):  # noqa: F811
    for task_id in ("task-1", "task-2"):
        issue_hook.owe(setup["intake"], "James3014/demo", 7, task_id)
    assert _owed(setup) == [f"James3014__demo__7__task-{n}{issue_hook.OWED}" for n in (1, 2)]
