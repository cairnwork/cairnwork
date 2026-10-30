"""#454: settle takes a running Issue's task from the ledger row, not the record alone: the row's
idempotency key and card hash must both be this Issue's (``gh:<repo>#<number>:``), and a ledger
sqlite cannot read is that Issue's error for this pass, as in take (#452)."""
# ruff: noqa: F811

import json

import pytest
from conftest import commit_change
from issue_helpers import _completed
from test_issue_adopt_request import _runner_payload
from test_issue_runner import FakeGitHub, _git_without_credentials, issue, setup  # noqa: F401

import cairnwork.issue_runner as runner
from cairnwork.intake import Intake

REPO = "James3014/demo"
SHA = "f" * 64
FOREIGN = {  # rows a client bound under the record's task id; none of them is Issue 7's
    "another Issue": (f"gh:{REPO}#70:{SHA[:16]}", f"gh:{REPO}#70:{SHA}"),
    "another repo": (f"gh:James3014/other#7:{SHA[:16]}", f"gh:James3014/other#7:{SHA}"),
    "a client key": ("client-key-1", f"gh:{REPO}#7:{SHA}"),
    "a client card": (f"gh:{REPO}#7:{SHA[:16]}", "client-card-1"),
    "a card of another Issue": (f"gh:{REPO}#7:{SHA[:16]}", f"gh:{REPO}#70:{SHA}"),
    "the Issue's key inside a client's": (f"c:gh:{REPO}#7:{SHA[:16]}", f"c:gh:{REPO}#7:{SHA}"),
}


@pytest.mark.parametrize("case", sorted(FOREIGN))
def test_a_record_naming_another_tasks_row_opens_no_pr(setup, monkeypatch, case):
    key, card = FOREIGN[case]
    bound = setup["intake"].submit(
        {**_runner_payload(setup), "idempotency_key": key, "card_hash": card}
    )
    runner._remember(setup["intake"], REPO, 7, bound["task_id"], SHA)  # a pre-#441 record
    github = FakeGitHub([issue()])
    github.labels[7] = {runner.RUNNING}
    commit = commit_change(setup["clone"], {"src/calc.py": "x = 2\n"})
    monkeypatch.setattr(Intake, "status", _completed(setup, bound, commit))
    monkeypatch.setattr(runner, "_git", _git_without_credentials)
    out = runner.settle(setup["intake"], github, setup["clone"])
    assert out == [
        {
            "issue": 7,
            "error": f"ValueError: ISSUE_TASK_MISMATCH: {bound['task_id']} is not "
            f"bound for gh:{REPO}#7",
        }
    ]
    assert github.prs == [] and github.comments == []
    assert github.labels[7] == {runner.RUNNING}


def test_a_record_naming_no_ledger_row_opens_no_pr(setup, monkeypatch):
    runner._remember(setup["intake"], REPO, 7, "task-" + "0" * 32, SHA)
    github = FakeGitHub([issue()])
    github.labels[7] = {runner.RUNNING}
    monkeypatch.setattr(
        Intake, "status", lambda self, **kw: {"status": "COMPLETED", "core_verified": True}
    )
    out = runner.settle(setup["intake"], github, setup["clone"])
    assert out[0]["error"].startswith("OwnershipUnknown: OWNERSHIP_UNKNOWN")
    assert github.prs == [] and github.labels[7] == {runner.RUNNING}


def test_the_issues_own_task_is_still_settled(setup, monkeypatch):
    github = FakeGitHub([issue()])
    taken = runner.take(setup["intake"], github, setup["clone"])[0]
    commit = commit_change(setup["clone"], {"src/calc.py": "x = 2\n"})
    monkeypatch.setattr(Intake, "status", _completed(setup, taken, commit))
    monkeypatch.setattr(runner, "_git", _git_without_credentials)
    out = runner.settle(setup["intake"], github, setup["clone"])
    assert out == [{"issue": 7, "pr": "https://github.com/James3014/demo/pull/1"}]


def _break_ledger(setup) -> None:
    state = setup["intake"].state
    for name in ("ownership.sqlite-wal", "ownership.sqlite-shm"):
        (state / name).unlink(missing_ok=True)
    (state / "ownership.sqlite").write_bytes(b"not a database" * 100)


def test_an_unreadable_ledger_is_each_running_issues_error(setup):
    github = FakeGitHub([issue(), issue(number=8)])
    runner.take(setup["intake"], github, setup["clone"])
    _break_ledger(setup)
    out = runner.settle(setup["intake"], github, setup["clone"])
    assert [r["issue"] for r in out] == [7, 8]
    assert all(r["error"].startswith("DatabaseError") for r in out)
    assert github.labels == {7: {runner.RUNNING}, 8: {runner.RUNNING}} and github.prs == []


def test_run_once_with_an_unreadable_ledger_prints_both_parts(setup, monkeypatch, capsys):
    github = FakeGitHub([issue(), issue(number=9)])
    github.labels[9] = set()  # queued only after the ledger broke
    runner.take(setup["intake"], github, setup["clone"])
    _break_ledger(setup)
    github.labels[9] = {runner.QUEUE}
    monkeypatch.setattr(runner, "GitHub", lambda gh, repo: github)
    argv = ["--state", str(setup["intake"].state), "--repo", REPO, "--clone", str(setup["clone"])]
    code = runner.main([*argv, "--gh", "/bin/nexus-agent-gh", "run-once"])
    printed = json.loads(capsys.readouterr().out)
    assert code == 1
    assert [r["issue"] for r in printed["take"]] == [9]
    assert printed["take"][0]["error"].startswith("DatabaseError")
    assert [r["issue"] for r in printed["settle"]] == [7]
    assert printed["settle"][0]["error"].startswith("DatabaseError")
