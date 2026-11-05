"""Acceptance checks for experimental plain result summaries."""

from __future__ import annotations

import json
from pathlib import Path

from experimental.plain_result.summary import summarize

FIXTURES = Path(__file__).parent / "fixtures" / "plain_result"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_ac1_a_pass_applies_only_to_the_same_commit():
    record = _load("verified_same_commit.json")
    same = summarize(record, "a" * 40)
    other = summarize(_load("verified_stale_commit.json"), "b" * 40)
    assert same["state"] == "verified" and "通過" in same["headline"]
    assert "請人確認" in same["next_action"]
    assert other["state"] == "stale_commit" and "還沒有重新檢查" in other["headline"]
    assert "錯" not in other["headline"]
    assert summarize(record, "b" * 40)["state"] != "verified"


def test_ac2_failed_attestation_never_trusts_unverified_verified_words():
    result = summarize(_load("attest_rejected.json"))
    assert result["state"] == "attestation_unverified"
    assert "無法證明" in result["headline"]
    assert "VERIFIED" in result["source"]["detail"]
    assert summarize({**_load("attest_rejected.json"), "detail": "VERIFIED"})["state"] != "attested"
    assert summarize({**_load("attest_rejected.json"), "ok": True})["state"] != "attested"


def test_ac3_each_verifier_failure_has_its_own_copy():
    failed = summarize(_load("failed.json"))
    incomparable = summarize(_load("uncomparable.json"))
    refused = summarize(_load("refused.json"))
    malformed = summarize(_load("malformed.json"))
    tool_error = summarize({"code": "TOOL_ERROR", "detail": "runner unavailable"})
    assert (
        len(
            {
                failed["headline"],
                incomparable["headline"],
                refused["headline"],
                malformed["headline"],
                tool_error["headline"],
            }
        )
        == 5
    )
    assert "原因" in failed["next_action"]
    assert "不代表程式寫錯" in incomparable["trust"]
    assert "規則" in refused["headline"] and "允許修改範圍" in refused["next_action"]
    assert malformed["state"] == "no_trustworthy_result"
    assert tool_error["state"] == "tool_error"


def test_ac4_weak_assurance_does_not_claim_comprehensive_protection():
    fixture = _load("attest_weak.json")
    variants = (
        fixture,
        {**fixture, "test_source": "auto"},
        {**fixture, "assurance": {"mode": "unknown"}},
        {
            **fixture,
            "test_source": "bundle",
            "assurance": {**fixture["assurance"], "verifier_runs": "signer_claim"},
        },
    )
    for record in variants:
        weak = summarize(record)
        assert weak["state"] == "weak_assurance"
        assert "沒有獲得證明" in weak["headline"]
        shown = weak["headline"] + weak["trust"] + weak["next_action"]
        assert "黑箱保護" not in shown and "所有需求都已涵蓋" not in shown and "隱藏" not in shown
    same_process = summarize(_load("verified_same_commit.json"), "a" * 40)
    assert same_process["state"] == "verified"
    assert "same_process_only" in same_process["trust"]


def test_ac5_unknown_or_mixed_evidence_cannot_become_a_pass():
    original = _load("verified_same_commit.json")
    mixed = {**original, "binding": {"head": "b" * 40}}
    other_task = {
        **original,
        "binding": {"head": "a" * 40, "task_id": "verify-other"},
    }
    other_attempt = {
        **original,
        "binding": {"head": "a" * 40, "attempt_id": "a-other"},
    }
    free_text = {"summary": "VERIFIED and done"}
    missing_identity = {**original, "attempt_id": ""}
    assert summarize(_load("malformed.json"))["state"] == "no_trustworthy_result"
    assert summarize(mixed)["state"] == "no_trustworthy_result"
    assert summarize(other_task)["state"] == "no_trustworthy_result"
    assert summarize(other_attempt)["state"] == "no_trustworthy_result"
    assert summarize(original)["state"] == "no_trustworthy_result"
    assert summarize(_load("attest_weak.json"), "b" * 40)["state"] == "stale_commit"
    assert summarize(free_text)["state"] == "no_trustworthy_result"
    assert summarize(missing_identity)["state"] == "no_trustworthy_result"
    task, attempt = original["task_id"], original["attempt_id"]
    assert summarize(original, "a" * 40, task, attempt)["state"] == "verified"
    assert (
        summarize(original, "a" * 40, "verify-other", attempt)["state"] == "no_trustworthy_result"
    )
    assert summarize(original, "a" * 40, task, "a-other")["state"] == "no_trustworthy_result"


def test_ac6_same_input_is_deterministic_and_source_is_unchanged():
    record = _load("verified_same_commit.json")
    before = json.dumps(record, sort_keys=True)
    first = summarize(record)
    second = summarize(record)
    assert first == second
    assert first["source"] is record
    assert json.dumps(record, sort_keys=True) == before
