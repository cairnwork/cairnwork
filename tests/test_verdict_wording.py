"""#700: every place that says "verified" carries the protection level, from one function."""

import pytest

from cairnwork import protection
from cairnwork.issue_publish import pr_body
from cairnwork_quickstart.cli import _report


@pytest.mark.parametrize("level", ["same_process_only", "partial", "blackbox"])
def test_the_issue_pr_body_says_the_level_beside_verified(level):
    body = pr_body(7, "t1", "a1", "c" * 40, {}, level)
    assert f"Core 判定 {protection.verdict_text(True, '', level)}（" in body
    assert f"VERIFIED {level}" in body


def test_the_issue_pr_body_without_a_level_says_plain_verified():
    assert "Core 判定 VERIFIED（" in pr_body(7, "t1", "a1", "c" * 40, {})


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
