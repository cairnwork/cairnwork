"""find_pr returns only this repo's PR into main at the notarized commit (#403).

A bare branch name matches every fork's branch of that name, so a fork PR from a branch named
`cairnwork/<N>-<attempt>` must never be reported as Cairnwork's fix.
"""
# ruff: noqa: F811

import json
import os
import subprocess

import pytest
from conftest import commit_change, git
from test_issue_runner import (  # noqa: F401
    FakeGitHub,
    _completed,
    _git_without_credentials,
    issue,
    setup,
)

import cairnwork.issue_runner as runner
from cairnwork.intake import Intake
from cairnwork.issue_github import GitHub

SHA = "a" * 40
OTHER = "b" * 40
BRANCH = f"cairnwork/7-a{'1' * 16}"


def pr(
    number,
    repo: str | None = "James3014/demo",
    sha=SHA,
    state="open",
    merged=None,
    ref=BRANCH,
    base="main",
):
    """One row of `GET /repos/{repo}/pulls`, as GitHub documents it."""
    return {
        "html_url": f"https://github.com/James3014/demo/pull/{number}",
        "state": state,
        "merged_at": merged,
        "head": {"ref": ref, "sha": sha, "repo": None if repo is None else {"full_name": repo}},
        "base": {"ref": base},
    }


@pytest.fixture
def fake_gh(tmp_path):
    """A `gh` that records its argv and prints whatever the test put in `listing`."""
    script = tmp_path / "gh"
    script.write_text(
        f'#!/bin/sh\nprintf "%s\\n" "$@" > "{tmp_path}/argv"\ncat "{tmp_path}/listing"\n'
    )
    os.chmod(script, 0o755)

    def run(listing, repo="James3014/demo"):
        (tmp_path / "listing").write_text(json.dumps(listing))
        return GitHub(str(script), repo), lambda: (tmp_path / "argv").read_text().splitlines()

    return run


def test_the_query_names_this_repos_owner_not_just_the_branch(fake_gh):
    github, argv = fake_gh([])
    assert github.find_pr(BRANCH, SHA) == ""
    assert argv() == [
        "api",
        "repos/James3014/demo/pulls?head=James3014%3Acairnwork%2F7-a1111111111111111"
        "&state=all&per_page=100",
    ]


def test_a_fork_pr_with_the_same_branch_name_is_never_returned(fake_gh):
    github, _ = fake_gh([pr(9, repo="mallory/demo"), pr(8, repo=None)])
    assert github.find_pr(BRANCH, SHA) == ""  # skipped: the runner pushes its own


def test_our_pr_at_another_commit_is_refused(fake_gh):
    github, _ = fake_gh([pr(3, sha=OTHER)])
    with pytest.raises(ValueError, match="PR_HEAD_MISMATCH"):
        github.find_pr(BRANCH, SHA)


def test_our_pr_closed_unmerged_is_refused(fake_gh):
    github, _ = fake_gh([pr(3, state="closed")])
    with pytest.raises(ValueError, match="PR_CLOSED"):
        github.find_pr(BRANCH, SHA)


@pytest.mark.parametrize("row", [pr(3), pr(3, state="closed", merged="2026-10-29T00:00:00Z")])
def test_our_open_or_merged_pr_at_the_notarized_commit_is_returned(fake_gh, row):
    github, _ = fake_gh([pr(9, repo="mallory/demo", sha=OTHER), pr(4, sha=OTHER), row])
    assert github.find_pr(BRANCH, SHA) == "https://github.com/James3014/demo/pull/3"


def test_the_repo_match_ignores_case_as_github_does(fake_gh):
    github, _ = fake_gh([pr(3, repo="james3014/Demo")])
    assert github.find_pr(BRANCH, SHA) == "https://github.com/James3014/demo/pull/3"


@pytest.mark.parametrize("row", [pr(3, ref="cairnwork/7-other"), pr(3, base="release")])
def test_another_branch_or_base_is_not_ours(fake_gh, row):
    github, _ = fake_gh([row])
    assert github.find_pr(BRANCH, SHA) == ""


@pytest.mark.parametrize("listing", [{"html_url": "x"}, ["x"], [{**pr(3), "html_url": 1}]])
def test_an_odd_answer_is_refused(fake_gh, listing):
    github, _ = fake_gh(listing)
    with pytest.raises(ValueError):
        github.find_pr(BRANCH, SHA)


def test_a_row_without_the_documented_head_is_not_ours(fake_gh):
    github, _ = fake_gh([{**pr(3), "head": "James3014:" + BRANCH}])
    assert github.find_pr(BRANCH, SHA) == ""


class ListingGitHub(FakeGitHub):
    """The fake GitHub, but find_pr is the real one over a canned pulls answer."""

    def __init__(self, listing):
        super().__init__([issue()])
        self.listing = listing

    def _run(self, *args, stdin=None):
        assert args[0] == "api" and "/pulls?" in args[1]
        return json.dumps(self.listing)

    def find_pr(self, branch, commit):
        return GitHub.find_pr(self, branch, commit)


def _settle(setup, monkeypatch, listing):
    github = ListingGitHub(listing)
    taken = runner.take(setup["intake"], github, setup["clone"])[0]
    commit = commit_change(setup["clone"], {"src/calc.py": "x = 2\n"})
    monkeypatch.setattr(Intake, "status", _completed(setup, taken, commit))
    monkeypatch.setattr(runner, "_git", _git_without_credentials)
    return github, commit, runner.settle(setup["intake"], github, setup["clone"])


def test_settle_pushes_and_opens_its_own_pr_past_a_fork_pr(setup, monkeypatch):
    github, commit, out = _settle(setup, monkeypatch, [pr(9, repo="mallory/demo", sha="0" * 40)])
    assert out[0]["pr"] == "https://github.com/James3014/demo/pull/1"
    assert [p[0] for p in github.prs] == [BRANCH]
    pushed = git(setup["origin"], "rev-parse", f"refs/heads/{BRANCH}")
    assert pushed == commit and github.labels[7] == {runner.DONE}
    assert "pull/9" not in github.comments[-1][1]


def test_settle_refuses_our_pr_at_another_commit(setup, monkeypatch):
    github, _, out = _settle(setup, monkeypatch, [pr(3, sha="0" * 40)])
    assert "PR_HEAD_MISMATCH" in out[0]["error"] and github.prs == []
    assert github.labels[7] == {runner.RUNNING}  # the Issue stays as it was
    assert git(setup["origin"], "for-each-ref", "refs/heads/cairnwork") == ""


def test_settle_reports_our_pr_at_the_notarized_commit_without_a_second_push(setup, monkeypatch):
    github = ListingGitHub([])
    taken = runner.take(setup["intake"], github, setup["clone"])[0]
    commit = commit_change(setup["clone"], {"src/calc.py": "x = 2\n"})
    github.listing = [pr(5, sha=commit)]
    monkeypatch.setattr(Intake, "status", _completed(setup, taken, commit))
    monkeypatch.setattr(runner, "_git", _git_without_credentials)
    out = runner.settle(setup["intake"], github, setup["clone"])
    assert out[0]["pr"] == "https://github.com/James3014/demo/pull/5" and github.prs == []
    assert subprocess.run(  # nothing was pushed: a previous pass did that
        ["git", "-C", str(setup["origin"]), "rev-parse", "--verify", "-q", f"refs/heads/{BRANCH}"],
        capture_output=True,
    ).returncode != 0  # fmt: skip
