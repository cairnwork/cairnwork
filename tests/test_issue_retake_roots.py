"""#671: a re-take finds a bound Issue in the ledger by its block text before parsing it, so a
test_roots change after take does not refuse it; new and changed blocks use the current roots."""
# ruff: noqa: F811

import json

import pytest
from test_issue_runner import BLOCK, FakeGitHub, issue, setup  # noqa: F401

import cairnwork.issue_runner as runner
from cairnwork.issue_task import IssueRefused, parse_task_block, task_block_sha256

PKG_BLOCK = {**BLOCK, "verification": ["python3 -m pytest -q pkg/tests/test_calc.py"]}


class RelabelFailsOnce(FakeGitHub):
    """The queue→running relabel fails on the first take, after the task was bound."""

    failed = False

    def relabel(self, number, add, remove):
        if not self.failed:
            self.failed = True
            raise RuntimeError("gh issue edit failed: HTTP 502")
        super().relabel(number, add, remove)


def _roots(intake, roots):
    (intake.state / "config.json").write_text(json.dumps({"test_roots": roots}))


def _bound_then_roots_changed(setup, body_block=PKG_BLOCK):
    intake = setup["intake"]
    _roots(intake, ["pkg/tests"])
    github = RelabelFailsOnce([issue(block=body_block)])
    first = runner.take(intake, github, setup["clone"])[0]
    assert "error" in first and github.labels[7] == {runner.QUEUE}
    assert len(setup["started"]) == 1
    _roots(intake, ["tests"])  # the verifier's root is no longer configured
    (task,) = intake.ledger.in_flight()
    return github, task.task_id


def test_a_bound_issue_is_adopted_after_the_test_roots_changed(setup):
    github, task_id = _bound_then_roots_changed(setup)
    again = runner.take(setup["intake"], github, setup["clone"])[0]
    assert again == {"issue": 7, "task_id": task_id, "already": True}
    assert github.labels[7] == {runner.RUNNING}
    assert len(setup["started"]) == 1 and github.comments == []  # no refusal comment
    record = json.loads(runner._record_path(setup["intake"], github.repo, 7).read_text())
    assert record["task_id"] == task_id


def test_a_changed_block_after_the_roots_changed_is_parsed_with_the_current_roots(setup):
    github, _ = _bound_then_roots_changed(setup)
    changed = {**PKG_BLOCK, "summary": "now also the other case"}
    github.bodies[7] = issue(block=changed)
    out = runner.take(setup["intake"], github, setup["clone"])[0]
    assert out["refused"].startswith("TASK_VERIFICATION_UNSUPPORTED"), out
    assert github.labels[7] == {runner.NEEDS_FIX}
    assert len(setup["started"]) == 1  # nothing new bound or started


def test_a_foreign_contract_fence_added_after_take_is_refused_on_retake(setup):
    """The eia-task block text is unchanged (same hash, same ledger row), but a ```*-contract
    fence now sits beside it: the re-take refuses the Issue instead of adopting the bound task."""
    github = RelabelFailsOnce([issue()])
    assert "error" in runner.take(setup["intake"], github, setup["clone"])[0]
    body = github.bodies[7]["body"] + "\n```nexus-contract\n{}\n```\n"
    github.bodies[7] = {**github.bodies[7], "body": body}
    out = runner.take(setup["intake"], github, setup["clone"])[0]
    assert out["refused"].startswith("TASK_BODY_HAS_FOREIGN_CONTRACT") and "already" not in out
    assert github.labels[7] == {runner.NEEDS_FIX}
    assert len(setup["started"]) == 1


def test_a_bound_block_parses_under_any_plain_directory_but_keeps_every_other_rule():
    body = issue(block=PKG_BLOCK)["body"]
    assert parse_task_block(body, None).verifiers == (
        ("python3", "-m", "pytest", "-q", "pkg/tests/test_calc.py"),
    )
    for bad in ("python3 -m pytest -q ../x/test_a.py", "python3 -m pytest -q test_a.py", "ls"):
        with pytest.raises(IssueRefused, match="TASK_VERIFICATION_UNSUPPORTED"):
            parse_task_block(issue(block={**BLOCK, "verification": [bad]})["body"], None)


def test_a_row_under_the_key_with_another_card_is_no_retake(setup):
    """Another block behind the same key prefix is not this block's row: the Issue is parsed with
    the current roots, so a verifier outside them is refused, not taken under any root."""
    body = issue(block=PKG_BLOCK)
    block = task_block_sha256(body["body"])
    setup["intake"].submit(
        {
            "idempotency_key": f"gh:James3014/demo#7:{block[:16]}",
            "card_hash": f"gh:James3014/demo#7:{'0' * 64}",
            "title": "another client",
            "instructions": "something else",
            "repo": str(setup["clone"]),
            "base_commit": runner._git(setup["clone"], "rev-parse", "origin/main").strip(),
            "allowed_files": ["src/calc.py"],
            "verifiers": [["pytest-0", ["python3", "-m", "pytest", "-q", "tests/test_calc.py"]]],
        }
    )
    github = FakeGitHub([body])
    out = runner.take(setup["intake"], github, setup["clone"])[0]
    assert out["refused"].startswith("TASK_VERIFICATION_UNSUPPORTED"), out
    assert github.labels[7] == {runner.NEEDS_FIX}
