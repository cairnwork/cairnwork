"""#700: every place that says "verified" carries the protection level, from one function."""

import pytest

from cairnwork import protection
from cairnwork.issue_publish import done_text, pr_body
from cairnwork.notary import _level
from cairnwork_quickstart.cli import _report


@pytest.mark.parametrize("level", ["same_process_only", "partial", "blackbox"])
def test_the_issue_pr_body_says_the_level_beside_verified(level):
    body = pr_body(7, "t1", "a1", "c" * 40, {}, level)
    assert f"Core 判定 {protection.verdict_text(True, '', level)}（" in body
    assert f"VERIFIED {level}" in body


def test_an_unknown_level_is_said_not_left_bare():
    assert "Core 判定 VERIFIED（保護等級未知）（" in pr_body(7, "t1", "a1", "c" * 40, {})
    assert "（VERIFIED（保護等級未知））" in done_text("", "o/r")
    assert "（VERIFIED partial）" in done_text("", "o/r", "partial")


def test_the_level_comes_from_the_requests_ids_not_the_binding():
    same = {"acceptance_contract": {"required_verifier_ids": ["pytest"]}}
    both = {"acceptance_contract": {"required_verifier_ids": ["blackbox:x", "pytest"]}}
    assert _level(same, None) == "same_process_only"
    assert _level(same, {"protection": "blackbox"}) == "same_process_only"  # coverage differs
    assert _level(same, {"protection": "bogus"}) == "same_process_only"
    assert _level(both, {"protection": "partial_macos"}) == "partial_macos"  # host grade kept
    assert _level(both, {"protection": "bogus"}) == protection.level(
        ["blackbox:x", "pytest"], macos=True
    )
    assert _level({}, {"protection": "blackbox"}) == ""
    none = {"acceptance_contract": {"required_verifier_ids": []}}
    assert _level(none, {"protection": "bogus"}) == ""  # no ids: never the binding's word
    assert _level(none, {"protection": "blackbox"}) == ""
    assert _level(same, {"protection": None}) == "same_process_only"
    assert _level({"acceptance_contract": "x"}, None) == ""


def test_the_quickstart_report_says_the_level(capsys):
    result = {"verified": True, "core_status": "VERIFIED", "protection": "partial"}
    assert _report(result, None) == 0
    assert "verdict:      VERIFIED partial ✓" in capsys.readouterr().out


def test_the_quickstart_report_for_a_failure_names_no_level(capsys):
    result = {"verified": False, "core_status": "FAILED", "protection": "partial"}
    assert _report(result, None) == 1
    assert "NOT VERIFIED ✗" in capsys.readouterr().out


def test_the_shared_verdict_text():
    assert protection.verdict_text(True, "FAILED", "partial") == "VERIFIED partial"
    assert protection.verdict_text(True, "", "") == "VERIFIED"
    assert protection.verdict_text(False, "FAILED", "partial") == "FAILED"
