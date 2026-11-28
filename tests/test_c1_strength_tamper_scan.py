"""Focused pins for rule construction and scan finding identity."""

import re

from cairnwork.tamper_scan import Finding, _rule, scan_added_lines


def test_rule_keeps_each_policy_field_and_compiles_patterns():
    rule = _rule(
        "sentinel",
        r"danger",
        skip_in_conftest=True,
        allow=r"^safe$",
        tests_only=True,
    )

    assert rule.name == "sentinel"
    assert rule.pattern.pattern == "danger"
    assert isinstance(rule.pattern, re.Pattern)
    assert rule.skip_in_conftest is True
    assert rule.allow is not None and rule.allow.pattern == "^safe$"
    assert isinstance(rule.allow, re.Pattern)
    assert rule.tests_only is True
    assert _rule("plain", r"ordinary").allow is None


def test_scan_budget_finding_preserves_source_path_and_line(monkeypatch):
    import cairnwork.tamper_scan as scanner
    from cairnwork.tamper_scan_budget import ScanBudget

    class NoTokens(ScanBudget):
        def tokenize(self, data):
            return False

    monkeypatch.setattr(scanner, "ScanBudget", NoTokens)
    found = scan_added_lines("pkg/target.py", [(17, "x = 1")], b"x = 1\n")

    assert found == [Finding("pkg/target.py", 17, "scan_budget")]
    empty = scan_added_lines("pkg/empty.py", [], b"")
    assert empty == [Finding("pkg/empty.py", 1, "scan_budget")]


def test_scan_budget_piece_finding_preserves_path_and_line(monkeypatch):
    import cairnwork.tamper_scan as scanner
    from cairnwork.tamper_scan_budget import ScanBudget

    class NoPieces(ScanBudget):
        def piece(self):
            return False

    monkeypatch.setattr(scanner, "ScanBudget", NoPieces)
    found = scan_added_lines("pkg/target.py", [(23, "x = 1")])

    assert found == [Finding("pkg/target.py", 23, "scan_budget")]
