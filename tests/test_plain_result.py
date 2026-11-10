"""Acceptance checks for experimental plain result summaries (#898 AC1-AC6)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from experimental.plain_result.summary import summarize

FIXTURES = Path(__file__).parent / "fixtures" / "plain_result"
A, B = "a" * 40, "b" * 40
STRONG_WORDS = ("黑箱保護", "所有需求都已涵蓋", "隱藏", "沒有缺陷", "一定正確")


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _shown(result: dict) -> str:
    return result["headline"] + result["trust"] + result["next_action"]


def _strong_attest() -> dict:
    """The weak fixture with every weak signal turned strong; only the producer cannot emit it."""
    record = _load("attest_weak.json")
    assurance = {**record["assurance"], "verifier_runs": "observed", "protection": "blackbox"}
    return {**record, "test_source": "bundle", "assurance": assurance}


def test_ac1_a_pass_applies_only_to_the_same_commit():
    record = _load("verified_same_commit.json")
    same = summarize(record, A)
    other = summarize(record, B)
    assert same["state"] == "verified" and "通過" in same["headline"]
    assert "請人確認" in same["next_action"]
    assert other["state"] == "stale_commit" and "還沒有重新檢查" in other["headline"]
    assert "錯" not in _shown(other)


@pytest.mark.parametrize(
    "change", [{"verified": False}, {"core_status": ""}, {"core_status": "FAILED"}]
)
def test_ac1_verified_needs_the_flag_and_core(change):
    assert summarize({**_load("verified_same_commit.json"), **change}, A)["state"] != "verified"


def test_ac2_failed_attestation_never_trusts_unverified_verified_words():
    result = summarize(_load("attest_rejected.json"))
    assert result["state"] == "attestation_unverified"
    assert "無法證明" in result["headline"]
    assert "VERIFIED" in result["source"]["detail"]
    for change in ({"detail": "VERIFIED"}, {"ok": True}, {"code": "OK"}):
        assert summarize({**_load("attest_rejected.json"), **change})["state"] not in (
            "attested",
            "weak_assurance",
        )


def test_ac3_each_verifier_failure_has_its_own_copy():
    failed = summarize(_load("failed.json"))
    incomparable = summarize(_load("uncomparable.json"))
    refused = summarize(_load("refused.json"))
    malformed = summarize(_load("malformed.json"))
    tool_error = summarize({"code": "TOOL_ERROR", "detail": "runner unavailable"})
    usage = summarize({**_load("attest_rejected.json"), "code": "USAGE"})
    results = (failed, incomparable, refused, malformed, tool_error)
    assert len({r["headline"] for r in results}) == 5
    assert "原因" in failed["next_action"]
    assert not any(word in _shown(failed) for word in ("AI", "寫錯", "錯誤的程式"))
    assert "不代表程式寫錯" in incomparable["trust"]
    assert "規則" in refused["headline"] and "允許修改範圍" in refused["next_action"]
    assert "NOT_ONE_COMMIT_ON_BASE" in refused["trust"]
    assert malformed["state"] == "no_trustworthy_result"
    assert tool_error["state"] == "tool_error" and usage["state"] == "tool_error"


@pytest.mark.parametrize("code", ["TASK_ALLOWED_FILES_PROTECTED", "VERIFIER_NOT_ALLOWED"])
def test_error_shaped_scope_refusal_is_not_checked(code):
    record = {
        "error": code,
        "detail": f"{code}: src/cairnwork/gitcmd.py: protected by policy; not a finding",
    }
    result = summarize(record)
    assert result["state"] == "not_checked"
    assert result["headline"] == "Cairnwork 沒有檢查這個改動。"
    assert "不是對改動的判斷" in result["trust"]
    assert "人工" in result["next_action"] or "人" in result["next_action"]


def test_other_error_shapes_fail_closed():
    assert summarize({"error": "RUNNER_CRASH", "detail": "stopped"})["state"] == "tool_error"
    for record in (
        {"error": 123},
        {"error": ""},
        {"error": "TASK_ALLOWED_FILES_PROTECTED", "status": "VERIFIED"},
    ):
        assert summarize(record)["state"] != "verified"
    assert summarize({"error": 123})["state"] == "no_trustworthy_result"
    assert summarize({"error": ""})["state"] == "no_trustworthy_result"
    assert (
        summarize({"error": "TASK_ALLOWED_FILES_PROTECTED", "status": "VERIFIED"})["state"]
        == "no_trustworthy_result"
    )


def test_scope_error_for_other_task_is_still_untrusted():
    record = {"error": "TASK_ALLOWED_FILES_PROTECTED", "task_id": "original"}
    assert summarize(record, task_id="different")["state"] == "no_trustworthy_result"


def test_verified_without_current_commit_names_unknown_version_fail_safely():
    record = _load("verified_same_commit.json")
    result = summarize(record, current_commit=None)
    assert result["state"] == "verified_version_unknown"
    assert record["candidate_commit"][:8] in result["headline"]
    assert "目前版本" in result["headline"] and "不能確認" in result["headline"]
    assert result["state"] != "verified"
    assert summarize({**record, "protection": "unknown"}, None)["state"] == (
        "no_trustworthy_result"
    )


@pytest.mark.parametrize(
    "change",
    [
        {"test_source": "auto"},
        {"assurance_mode": "unknown"},
        {"assurance_mode": "signer_claim"},
        {"assurance_protection": "same_process_only"},
        {"assurance_verifier_runs": "signer_claim"},
        {"assurance_test_contents": "not_verified"},
        {"assurance": None},
    ],
)
def test_ac4_each_weak_signal_alone_gives_the_weak_text(change):
    record = _strong_attest()
    assert summarize(record)["state"] == "attested"
    for key, value in change.items():
        if key.startswith("assurance_"):
            record = {
                **record,
                "assurance": {**record["assurance"], key[len("assurance_") :]: value},
            }
        else:
            record = {**record, key: value}
    result = summarize(record)
    assert result["state"] in ("weak_assurance", "no_trustworthy_result")
    assert result["state"] != "attested"


def test_ac4_no_state_makes_a_strong_claim():
    records = [_load(p.name) for p in sorted(FIXTURES.glob("*.json"))] + [_strong_attest()]
    for record in records:
        for current in (None, A, B):
            assert not any(word in _shown(summarize(record, current)) for word in STRONG_WORDS)
    weak = summarize(_load("attest_weak.json"))
    assert weak["state"] == "weak_assurance" and "沒有獲得證明" in weak["headline"]
    assert "same_process_only" in summarize(_load("verified_same_commit.json"), A)["trust"]


def test_ac5_unknown_or_mixed_evidence_cannot_become_a_pass():
    original = _load("verified_same_commit.json")
    assert summarize(original, A)["state"] == "verified"  # the guards below are what reject
    bad = [
        {**original, "binding": {"head": B}},
        {**original, "binding": {"head": A, "task_id": "verify-other"}},
        {**original, "binding": {"head": A, "attempt_id": "a-other"}},
        {**original, "binding": "not a dict"},
        {**original, "attempt_id": ""},
        {**original, "task_id": ""},
        {**original, "candidate_commit": "a" * 39},
        {"summary": "VERIFIED and done"},
        _load("malformed.json"),
    ]
    for record in bad:
        assert summarize(record, A)["state"] == "no_trustworthy_result", record
    assert summarize("VERIFIED", A)["state"] == "no_trustworthy_result"  # type: ignore[arg-type]
    assert summarize(original, "not-a-commit")["state"] == "no_trustworthy_result"
    assert summarize(original)["state"] == "verified_version_unknown"
    task, attempt = original["task_id"], original["attempt_id"]
    assert summarize(original, A, task, attempt)["state"] == "verified"
    assert summarize(original, A, "verify-other", attempt)["state"] == "no_trustworthy_result"
    assert summarize(original, A, task, "a-other")["state"] == "no_trustworthy_result"
    assert summarize(_load("attest_weak.json"), B)["state"] == "stale_commit"


def test_ac6_same_input_is_deterministic_and_source_is_unchanged():
    record = _load("verified_same_commit.json")
    before = json.dumps(record, sort_keys=True)
    first = summarize(record, A)
    second = summarize(record, A)
    assert first == second
    assert first["source"] is record
    assert json.dumps(record, sort_keys=True) == before
