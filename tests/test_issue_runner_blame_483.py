import json

import pytest
from conftest import git
from test_issue_runner import FakeGitHub, issue
from test_issue_runner import setup as setup  # noqa: F401

import cairnwork.issue_runner as runner
from cairnwork.gitcmd import GitCommandError
from cairnwork.pipeline import TaskRejected
from cairnwork.taskpaths import check_task_paths


@pytest.mark.parametrize("status", ["DISPATCHED", "COMPLETED"])
def test_bad_stored_request_keeps_live_issue_queued(setup, status):
    intake, clone = setup["intake"], setup["clone"]
    github = FakeGitHub([issue()])
    taken = runner.take(intake, github, clone)[0]
    path = intake.state / "requests" / f"{taken['task_id']}.json"
    path.write_text(json.dumps({**json.loads(path.read_text()), "unexpected": True}))
    intake.ledger.set_status(taken["task_id"], "DISPATCHED", expected_from="BOUND")
    if status == "COMPLETED":
        intake.ledger.set_status(taken["task_id"], "COMPLETED", expected_from="DISPATCHED")
    github.labels[7] = {runner.QUEUE}

    result = runner.take(intake, github, clone)

    assert "error" in result[0]
    assert github.labels[7] == {runner.QUEUE}
    assert intake.ledger.get(task_id=taken["task_id"]).status == status


def test_bad_issue_block_still_gets_needs_fix(setup):
    bad = {"difficulty": "easy", "allowed_files": ["src/calc.py"]}
    github = FakeGitHub([issue(block=bad)])
    result = runner.take(setup["intake"], github, setup["clone"])
    assert "refused" in result[0]
    assert github.labels[7] == {runner.NEEDS_FIX}


def test_git_timeout_does_not_blame_issue(setup, monkeypatch):
    github = FakeGitHub([issue()])
    monkeypatch.setattr(
        runner, "_verify_origin", lambda clone, repo: f"https://github.com/{repo}.git"
    )
    monkeypatch.setattr(runner, "_verify_single_branch", lambda clone: None)
    monkeypatch.setattr(runner, "_git", lambda *a, **k: "a" * 40)
    monkeypatch.setattr(
        runner,
        "_take_one",
        lambda *a: (_ for _ in ()).throw(RuntimeError("git could not inspect base tree: timeout")),
    )
    result = runner.take(setup["intake"], github, setup["clone"])
    assert "error" in result[0]
    assert github.labels[7] == {runner.QUEUE}


def test_missing_path_at_commit_remains_task_rejected(repo):
    commit = git(repo, "rev-parse", "HEAD").strip()
    with pytest.raises(TaskRejected) as caught:
        check_task_paths(repo, commit, (), ("tests/not_here.py",))
    assert caught.value.code == "TASK_VERIFIER_TARGET_MISSING"


def test_git_command_failure_propagates_as_non_refusal(repo, monkeypatch):
    def timeout(*args, **kwargs):
        raise GitCommandError("git ls-tree failed: timeout")

    monkeypatch.setattr("cairnwork.taskpaths.run_git", timeout)
    with pytest.raises(RuntimeError):
        check_task_paths(repo, "a" * 40, ("src/calc.py",), ())
