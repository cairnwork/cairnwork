"""Acceptance outcomes identity checks against the verify result."""

from __future__ import annotations

import json
from typing import Any

import pytest
from test_acceptance_outcomes import fx, inputs, verified  # noqa: F401

from experimental.acceptance_outcomes import OutcomesError, record_call


def result_for(fx: dict[str, Any], **updates: Any) -> dict[str, Any]:  # noqa: F811
    return {
        **verified(fx),
        "candidate_commit": fx["cand"],
        "executed_tree": "tree-1",
        "binding": {
            "base": fx["base"],
            "test_commit": "tests-1",
            "executed_tree": "tree-1",
        },
        **updates,
    }


@pytest.mark.parametrize(
    ("field", "result_field", "wrong"),
    [
        ("candidate", "candidate_commit", "1" * 40),
        ("base", "base", "2" * 40),
        ("test_commit", "test_commit", "3" * 40),
        ("repo", "repo", "/elsewhere"),  # review nit
    ],
)
def test_result_identity_mismatch_refused_with_notary_lost(
    fx: dict[str, Any],  # noqa: F811
    field: str,
    result_field: str,
    wrong: str,
) -> None:
    result = result_for(fx)
    if result_field in ("base", "test_commit"):
        result["binding"][result_field] = wrong
    else:
        result[result_field] = wrong
    with pytest.raises(OutcomesError) as error:
        record_call(fx["led"], result, inputs(fx, test_commit="tests-1"), notary_lost="gone")
    assert error.value.code == "OUTCOME_RECORD_MISMATCH"
    assert str(error.value) == f"OUTCOME_RECORD_MISMATCH: {field} differs from the result"
    assert not fx["led"].exists()


def test_result_identity_mismatch_refused_with_notary_db(fx: dict[str, Any]) -> None:  # noqa: F811
    result = result_for(fx, candidate_commit="4" * 40)
    with pytest.raises(OutcomesError, match="OUTCOME_RECORD_MISMATCH: candidate differs"):
        record_call(fx["led"], result, inputs(fx, test_commit="tests-1"), fx["db"])
    assert not fx["led"].exists()


@pytest.mark.parametrize("mode", ["notary_lost", "notary_db"])
def test_matching_result_keeps_call_fields_and_records_tree(
    fx: dict[str, Any],  # noqa: F811
    mode: str,
) -> None:
    result = result_for(fx)
    kwargs = {"notary_lost": "gone"} if mode == "notary_lost" else {}
    db = None if mode == "notary_lost" else fx["db"]
    assert (
        record_call(fx["led"], result, inputs(fx, test_commit="tests-1"), db, **kwargs) == "ADDED"
    )
    row = json.loads(fx["led"].read_text())
    assert {
        key: row[key]
        for key in (
            "repo",
            "base",
            "candidate",
            "version",
            "test_commit",
            "verdict",
            "reason",
            "task_id",
            "attempt_id",
        )
    } == {
        "repo": "r",
        "base": fx["base"],
        "candidate": fx["cand"],
        "version": "v1",
        "test_commit": "tests-1",
        "verdict": "VERIFIED",
        "reason": "",
        "task_id": "task-1",
        "attempt_id": "a1",
    }
    assert row["executed_tree"] == "tree-1"


def test_a_bound_head_other_than_the_candidate_is_refused(fx: dict[str, Any]) -> None:  # noqa: F811
    result = result_for(fx)
    result["binding"]["head"] = "5" * 40
    with pytest.raises(OutcomesError, match="OUTCOME_RECORD_MISMATCH: candidate differs"):
        record_call(fx["led"], result, inputs(fx, test_commit="tests-1"), notary_lost="gone")


def test_an_omitted_test_commit_is_taken_from_the_result(fx: dict[str, Any]) -> None:  # noqa: F811
    assert record_call(fx["led"], result_for(fx), inputs(fx), notary_lost="gone") == "ADDED"
    assert json.loads(fx["led"].read_text())["test_commit"] == "tests-1"
