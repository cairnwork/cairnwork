"""Rerun paths of the Issue runner: a pass finds the PR an earlier pass already opened (#709)."""
# ruff: noqa: F811

from conftest import commit_change
from issue_helpers import _completed
from test_issue_runner import FakeGitHub, _git_without_credentials, issue, setup  # noqa: F401

import cairnwork.issue_runner as runner
from cairnwork.intake import Intake


def test_settle_after_a_partial_failure_reuses_the_pr(setup, monkeypatch):
    github = FakeGitHub([issue()])
    taken = runner.take(setup["intake"], github, setup["clone"])[0]
    commit = commit_change(setup["clone"], {"src/calc.py": "x = 2\n"})
    monkeypatch.setattr(Intake, "status", _completed(setup, taken, commit))
    monkeypatch.setattr(runner, "_git", _git_without_credentials)
    github.prs.append((f"cairnwork/7-a{'1' * 16}", "t", "b"))  # opened by a pass that then died
    out = runner.settle(setup["intake"], github, setup["clone"])
    assert out[0]["pr"] == "https://github.com/James3014/demo/pull/1" and len(github.prs) == 1


def test_a_rerun_with_an_existing_pr_still_reports_the_level(setup, monkeypatch):
    """#709: the level is computed before find_pr, so the rerun's done comment carries it; the
    existing PR is not rewritten."""
    github = FakeGitHub([issue()])
    taken = runner.take(setup["intake"], github, setup["clone"])[0]
    commit = commit_change(setup["clone"], {"src/calc.py": "x = 2\n"})
    monkeypatch.setattr(Intake, "status", _completed(setup, taken, commit))
    monkeypatch.setattr(runner, "_git", _git_without_credentials)
    github.prs.append((f"cairnwork/7-a{'1' * 16}", "t", "old body"))
    out = runner.settle(setup["intake"], github, setup["clone"])
    assert out[0]["pr"].endswith("/pull/1") and len(github.prs) == 1
    assert github.prs[0][2] == "old body"  # not rewritten
    assert "（VERIFIED same_process_only）" in github.comments[-1][1]


def test_a_rerun_without_a_verdict_says_unknown_never_bare(setup, monkeypatch):
    github = FakeGitHub([issue()])
    taken = runner.take(setup["intake"], github, setup["clone"])[0]
    commit = commit_change(setup["clone"], {"src/calc.py": "x = 2\n"})
    monkeypatch.setattr(Intake, "status", _completed(setup, taken, commit))
    monkeypatch.setattr(runner, "_git", _git_without_credentials)
    monkeypatch.setattr(type(setup["intake"].notary), "verdict", lambda *a, **k: None)
    github.prs.append((f"cairnwork/7-a{'1' * 16}", "t", "old body"))
    runner.settle(setup["intake"], github, setup["clone"])
    text = github.comments[-1][1]
    assert "保護等級未知" in text
    assert "（VERIFIED）" not in text
