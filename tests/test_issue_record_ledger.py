"""#442: the per-Issue record is a cache; only the ledger row under take's key, checked by
``adoptable``, adopts a task. A record that names the current block proves nothing by itself."""
# ruff: noqa: F811

import json

import pytest
from test_issue_adopt_request import CHANGES, _runner_payload
from test_issue_runner import FakeGitHub, issue, setup  # noqa: F401

import cairnwork.issue_runner as runner
from cairnwork.issue_task import parse_task_block

REPO = "James3014/demo"
BLOCK_SHA = parse_task_block(issue()["body"]).block_sha256


def _record(setup) -> dict:
    return json.loads(runner._record_path(setup["intake"], REPO, 7).read_text())


def test_a_record_without_a_ledger_row_is_not_adopted(setup):
    """A record naming the current block but no row under the key: the ledger decides (submit)."""
    runner._remember(setup["intake"], REPO, 7, "forged-task", BLOCK_SHA)
    github = FakeGitHub([issue()])
    out = runner.take(setup["intake"], github, setup["clone"])[0]
    assert "already" not in out and out["task_id"] != "forged-task"
    assert out["duplicate"] is False and len(setup["started"]) == 1
    assert _record(setup)["task_id"] == out["task_id"]
    assert setup["intake"].ledger.get(task_id=out["task_id"]).card_hash.endswith(BLOCK_SHA)


@pytest.mark.parametrize("field", sorted(CHANGES))
def test_a_record_over_a_pre_bound_request_is_not_adopted(setup, field):
    """What a pre-#441 take wrote: a record adopted by key and card only, over a client's row."""
    pre = setup["intake"].submit({**_runner_payload(setup), **CHANGES[field]})
    runner._remember(setup["intake"], REPO, 7, pre["task_id"], BLOCK_SHA)
    started, bound = len(setup["started"]), len(setup["intake"].ledger.in_flight())
    github = FakeGitHub([issue()])
    out = runner.take(setup["intake"], github, setup["clone"])[0]
    assert "already" not in out and "task_id" not in out
    assert out["refused"].startswith(f"TASK_BOUND_REQUEST_DIFFERS: {pre['task_id']}")
    assert field in out["refused"]
    assert github.labels[7] == {runner.NEEDS_FIX}
    assert "TASK_BOUND_REQUEST_DIFFERS" in github.comments[-1][1]
    assert len(setup["started"]) == started
    assert len(setup["intake"].ledger.in_flight()) == bound
    assert runner.settle(setup["intake"], github, setup["clone"]) == [] and github.prs == []


def test_a_record_naming_another_task_is_rewritten_from_the_ledger(setup):
    github = FakeGitHub([issue()])
    first = runner.take(setup["intake"], github, setup["clone"])[0]
    runner._remember(setup["intake"], REPO, 7, "task-other", BLOCK_SHA)
    github.labels[7] = {runner.QUEUE}
    out = runner.take(setup["intake"], github, setup["clone"])
    assert out == [{"issue": 7, "task_id": first["task_id"], "already": True}]
    assert _record(setup) == {
        "repo": REPO,
        "issue": 7,
        "task_id": first["task_id"],
        "block_sha256": BLOCK_SHA,
    }
    assert github.labels[7] == {runner.RUNNING} and len(setup["started"]) == 1


def test_a_matching_record_with_an_unreadable_request_stays_queued(setup):
    """BOUND row, request file gone: this pass's error, not an adoption (FAILED is #443's case)."""
    github = FakeGitHub([issue()])
    first = runner.take(setup["intake"], github, setup["clone"])[0]
    (setup["intake"].state / "requests" / f"{first['task_id']}.json").unlink()
    github.labels[7] = {runner.QUEUE}
    out = runner.take(setup["intake"], github, setup["clone"])[0]
    assert out["error"].startswith("RequestUnreadable") and "already" not in out  # #466: typed
    assert github.labels[7] == {runner.QUEUE} and len(github.comments) == 1
    assert len(setup["started"]) == 1


def test_a_matching_record_with_an_unreadable_ledger_stays_queued(setup):
    github = FakeGitHub([issue()])
    runner.take(setup["intake"], github, setup["clone"])
    state = setup["intake"].state
    for name in ("ownership.sqlite-wal", "ownership.sqlite-shm"):
        (state / name).unlink(missing_ok=True)
    (state / "ownership.sqlite").write_bytes(b"not a database" * 100)
    github.labels[7] = {runner.QUEUE}
    out = runner.take(setup["intake"], github, setup["clone"])[0]
    assert out["error"].startswith("DatabaseError") and "already" not in out
    assert github.labels[7] == {runner.QUEUE} and len(setup["started"]) == 1


def test_adoption_rewrites_the_record_before_the_issue_is_labelled_running(setup):
    """Settle reads the record of a running Issue: it must name the adopted task by then."""
    seen = []

    class Watching(FakeGitHub):
        def relabel(self, number, add, remove):
            if add == runner.RUNNING:
                seen.append(_record(setup)["task_id"])
            super().relabel(number, add, remove)

    github = Watching([issue()])
    first = runner.take(setup["intake"], github, setup["clone"])[0]
    runner._remember(setup["intake"], REPO, 7, "task-other", BLOCK_SHA)
    github.labels[7] = {runner.QUEUE}
    assert runner.take(setup["intake"], github, setup["clone"])[0]["already"] is True
    assert seen == [first["task_id"], first["task_id"]]
