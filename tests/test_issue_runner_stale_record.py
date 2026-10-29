"""#424: an Issue edited after a run whose record write failed is found in the ledger, not stuck."""
# ruff: noqa: F811

import json

from conftest import commit_change, git
from test_issue_runner import BLOCK, FakeGitHub, issue, setup  # noqa: F401

import cairnwork.issue_runner as runner


def test_an_edited_issue_whose_record_write_failed_is_found_in_the_ledger(setup, monkeypatch):
    """#424: submit bound block B, the record write failed; the old (block A) record is intact."""
    import errno

    github = FakeGitHub([issue()])
    first = runner.take(setup["intake"], github, setup["clone"])[0]
    changed = issue(block={**BLOCK, "allowed_files": ["src/calc.py", "src/more.py"]})
    github.bodies[7], github.labels[7] = changed, {runner.QUEUE}
    real_mkstemp = runner.tempfile.mkstemp

    def disk_full(*_a, **_k):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(runner.tempfile, "mkstemp", disk_full)
    assert "error" in runner.take(setup["intake"], github, setup["clone"])[0]
    monkeypatch.setattr(runner.tempfile, "mkstemp", real_mkstemp)
    started, bound = len(setup["started"]), len(setup["intake"].ledger.in_flight())
    commit_change(setup["clone"], {"other.txt": "main moved\n"})
    git(setup["clone"], "push", "-q", "origin", "HEAD:main")
    # the record still names block A's task; the ledger holds block B's
    assert (
        json.loads(runner._record_path(setup["intake"], github.repo, 7).read_text())["task_id"]
        == first["task_id"]
    )
    again = runner.take(setup["intake"], github, setup["clone"])[0]
    assert again["already"] is True and again["task_id"] != first["task_id"]
    assert github.labels[7] == {runner.RUNNING}
    assert len(setup["started"]) == started  # nothing was bound or started a second time
    assert len(setup["intake"].ledger.in_flight()) == bound  # no second task for the block
    assert runner.take(setup["intake"], github, setup["clone"]) == []  # no longer queued
