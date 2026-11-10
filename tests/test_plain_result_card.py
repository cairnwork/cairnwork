"""The evidence card (#898 follow-up): four cells, each line traceable to the record, never more
certain than ``summarize``."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from cairnwork.reasons import explain, why_not_passed
from experimental.plain_result.card import card

FIXTURES = Path(__file__).parent / "fixtures" / "plain_result"
A, B = "a" * 40, "b" * 40
STRONG_WORDS = (
    "黑箱保護",
    "所有需求都已涵蓋",
    "全部需求都測過了",
    "沒有缺陷",
    "一定正確",
    "功能正確",
)
CELLS = ("tested", "confirmed_problem", "not_covered", "undetermined")
RUN = {"ran_sandboxed": True, "returncode": 1, "timed_out": False, "mutated": (), "survivors": 0,
       "log_too_big": False, "tests_ran": True}  # fmt: skip


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _at(record: dict, path: str):
    value = record
    for key in path.split("."):
        assert isinstance(value, dict) and key in value, path
        value = value[key]
    return value


def _check(result: dict) -> None:
    """Every card: "not covered" is never empty, every tested or confirmed line names a field
    that exists in the record, and nothing claims more than was checked."""
    assert result["not_covered"] and "未知" in result["not_covered"][0]["text"]
    for cell in ("tested", "confirmed_problem"):
        for line in result[cell]:
            _at(result["source"], line["source"])
    text = "".join(line["text"] for cell in CELLS for line in result[cell])
    assert not any(word in text for word in STRONG_WORDS)


def _verified_bound() -> dict:
    binding = {"head": A, "verifiers": [["unit", ["python", "-m", "pytest"]]],
               "test_paths": ["tests/test_x.py"]}  # fmt: skip
    return {**_load("verified_same_commit.json"), "binding": binding}


def test_a_pass_fills_tested_from_its_fields_only():
    result = card(_verified_bound(), A)
    _check(result)
    sources = [line["source"] for line in result["tested"]]
    assert sources == ["candidate_commit", "core_status", "protection", "binding.verifiers",
                       "binding.test_paths"]  # fmt: skip
    assert "same_process_only" in result["tested"][2]["text"]
    assert result["confirmed_problem"] == [] and result["undetermined"] == []


@pytest.mark.parametrize("current", [None, B])
def test_a_pass_not_for_this_commit_tests_nothing(current):
    result = card(_load("verified_same_commit.json"), current)
    _check(result)
    assert result["tested"] == [] and result["undetermined"]


def _failed(**run) -> dict:
    """A FAILED record whose reason the real producers wrote (reasons.why_not_passed, explain)."""
    why = why_not_passed(**{**RUN, **run})
    failed = SimpleNamespace(spec=SimpleNamespace(verifier_id="v"), why=why, notes=(),
                             observation=SimpleNamespace(passed=False))  # fmt: skip
    return {**_load("failed.json"), "reason": "CORE_FAILED" + explain([failed])}


def test_a_plain_test_failure_is_a_problem_not_yet_cleared_of_the_environment():
    result = card(_failed(), B)
    _check(result)
    assert result["confirmed_problem"][0]["source"] == "reason"
    assert "尚未排除環境因素" in result["confirmed_problem"][0]["text"]
    assert result["tested"] == [] and result["undetermined"] == []


@pytest.mark.parametrize(
    "run",
    [
        {"fail_kind": {"t::a": "env"}},
        {"fail_kind": {"t::a": "setup"}},
        {"fail_kind": {"t::a": "plugin"}},
        {"denied_path": "/private/var/x"},
        {"ran_sandboxed": False},
        {"timed_out": True},
        {"log_too_big": True},
        {"report_too_big": True},
        {"survivors": 1},
        {"exitstatus": 0, "testsfailed": 0},
        {"returncode": 0, "tests_ran": False},
        {"returncode": 2},
    ],
    ids=["env", "setup", "plugin", "denied", "no-sandbox", "timeout", "log-cap", "report-cap",
         "survivors", "status-disagrees", "no-test-ran", "usage-error"],
)  # fmt: skip
def test_any_other_failure_cause_is_undetermined(run):
    """Only "pytest exit code 1" alone is a test failure; every other cause the real producer
    writes goes to undetermined, so a new cause there is undetermined by default."""
    result = card(_failed(**run))
    _check(result)
    assert result["confirmed_problem"] == [] and result["undetermined"]


OTHER_REASONS = [
    None,
    [],
    "",
    "verifier returned nonzero",
    "CORE_FAILED [v: pytest exit code 1] [x]",
]


@pytest.mark.parametrize("reason", OTHER_REASONS)
def test_a_reason_of_another_type_or_shape_is_undetermined(reason):
    result = card({**_load("failed.json"), "reason": reason})
    _check(result)
    assert result["confirmed_problem"] == []


def test_a_failure_of_another_commit_is_not_a_problem_of_this_one():
    result = card(_failed(), A)
    _check(result)
    assert result["confirmed_problem"] == []


def test_a_test_path_string_is_not_split_into_characters():
    record = _verified_bound()
    record["binding"] = {**record["binding"], "test_paths": "tests/test_x.py"}
    result = card(record, A)
    assert [line["source"] for line in result["tested"]][-1] == "binding.verifiers"


@pytest.mark.parametrize(
    "name, words",
    [("uncomparable.json", "BASE_CHANGED"), ("refused.json", "NOT_ONE_COMMIT_ON_BASE"),
     ("attest_weak.json", "signer_claim"), ("malformed.json", "")],
)  # fmt: skip
def test_no_verdict_goes_to_undetermined(name, words):
    result = card(_load(name))
    _check(result)
    assert result["tested"] == [] and result["confirmed_problem"] == []
    assert words in result["undetermined"][0]["text"]


def test_a_refusal_before_core_is_undetermined_with_its_code():
    result = card({"error": "PROTECTED_PATH", "detail": "tests/x"})
    _check(result)
    assert result["undetermined"] == [{"text": "沒有執行驗收：PROTECTED_PATH", "source": "error"}]


def test_scope_refusal_and_unknown_version_stay_undetermined():
    refusal = card(
        {
            "error": "TASK_ALLOWED_FILES_PROTECTED",
            "detail": "TASK_ALLOWED_FILES_PROTECTED: src/cairnwork/gitcmd.py: protected by policy",
        }
    )
    unknown_version = card(_load("verified_same_commit.json"), current_commit=None)
    for result in (refusal, unknown_version):
        _check(result)
        assert result["tested"] == [] and result["confirmed_problem"] == []
        assert result["undetermined"]
    assert refusal["state"] == "not_checked"
    assert refusal["undetermined"][0]["source"] == "error"
    assert unknown_version["state"] == "verified_version_unknown"
    assert unknown_version["undetermined"][0]["source"] == "candidate_commit"


def test_a_record_for_other_work_tests_nothing():
    result = card(_verified_bound(), A, task_id="verify-other")
    _check(result)
    assert result["tested"] == []
