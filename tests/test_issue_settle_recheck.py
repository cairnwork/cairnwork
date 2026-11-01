"""settle re-checks the Issue take bound before it opens a PR (#624), as take does before dispatch.

Fake GitHub and a real local clone (no network), as in test_issue_runner."""

from conftest import commit_change
from issue_helpers import _completed
from test_issue_runner import BLOCK, FakeGitHub, _git_without_credentials, issue
from test_issue_runner import setup as setup  # noqa: F401 - fixture

import cairnwork.issue_runner as runner
from cairnwork.intake import Intake


def _verified(setup, monkeypatch, github):  # noqa: F811
    taken = runner.take(setup["intake"], github, setup["clone"])[0]
    assert github.labels[7] == {runner.RUNNING}
    commit = commit_change(setup["clone"], {"src/calc.py": "x = 2\n"})
    monkeypatch.setattr(Intake, "status", _completed(setup, taken, commit))
    monkeypatch.setattr(runner, "_git", _git_without_credentials)


def _refused(github, setup, code):  # noqa: F811
    out = runner.settle(setup["intake"], github, setup["clone"])
    assert out == [{"issue": 7, "error": f"RuntimeError: {code}"}], out
    assert github.prs == []
    assert github.labels[7] == {runner.NEEDS_FIX}
    assert code in github.comments[-1][1] and runner.QUEUE in github.comments[-1][1]
    return out


def test_a_block_edited_while_running_gets_no_pr(setup, monkeypatch):  # noqa: F811
    github = FakeGitHub([issue()])
    _verified(setup, monkeypatch, github)
    github.bodies[7] = issue(block={**BLOCK, "allowed_files": ["src/calc.py", "src/other.py"]})
    _refused(github, setup, "ISSUE_CHANGED_AFTER_TAKE")
    # the next pass leaves it alone: it is no longer running
    assert runner.settle(setup["intake"], github, setup["clone"]) == []


def test_a_block_that_no_longer_parses_gets_no_pr(setup, monkeypatch):  # noqa: F811
    github = FakeGitHub([issue()])
    _verified(setup, monkeypatch, github)
    github.bodies[7] = {**issue(), "body": "no task block any more"}
    _refused(github, setup, "ISSUE_CHANGED_AFTER_TAKE")


def test_queueing_no_longer_trusted_gets_no_pr(setup, monkeypatch):  # noqa: F811
    github = FakeGitHub([issue()])
    _verified(setup, monkeypatch, github)
    github.trusted = "BODY_EDITED_BY_UNTRUSTED"
    _refused(github, setup, "BODY_EDITED_BY_UNTRUSTED")


def test_an_unchanged_trusted_issue_still_gets_its_pr(setup, monkeypatch):  # noqa: F811
    github = FakeGitHub([issue()])
    _verified(setup, monkeypatch, github)
    github.bodies[7] = {**issue(), "title": "fix x (title edited by a trusted person)"}
    out = runner.settle(setup["intake"], github, setup["clone"])
    assert out and out[0].get("pr"), out
    assert github.labels[7] == {runner.DONE}


class _OrderedGitHub(FakeGitHub):
    """Records whether the body was read after a refused trust check."""

    def __init__(self, issues):
        super().__init__(issues)
        self.calls = []
        read = self.current
        self.current = lambda number: self.calls.append("current") or read(number)

    def trust(self, number, label):
        self.calls.append("trust")
        return self.trusted


def test_untrusted_queueing_is_refused_before_the_body_is_read(setup, monkeypatch):  # noqa: F811
    github = _OrderedGitHub([issue()])
    _verified(setup, monkeypatch, github)
    github.calls.clear()
    github.trusted = "BODY_EDITED_BY_UNTRUSTED"
    github.bodies[7] = issue(block={**BLOCK, "allowed_files": [["src/calc.py"]]})
    _refused(github, setup, "BODY_EDITED_BY_UNTRUSTED")
    assert github.calls == ["trust"]


def test_a_block_that_cannot_be_parsed_at_all_counts_as_changed(setup, monkeypatch):  # noqa: F811
    github = FakeGitHub([issue()])
    _verified(setup, monkeypatch, github)
    github.bodies[7] = issue(block={**BLOCK, "allowed_files": [["src/calc.py"]]})

    def unreadable(*_a, **_k):
        raise RecursionError("maximum recursion depth exceeded")

    monkeypatch.setattr(runner.issue_task, "parse_task_block", unreadable)
    _refused(github, setup, "ISSUE_CHANGED_AFTER_TAKE")
