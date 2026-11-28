"""Second-batch survivor pins for tamper_scan's rule defaults and committed-file path."""

import sys

from conftest import commit_change, git

import cairnwork.tamper_scan as scan
from cairnwork.tamper_scan import Finding, _rule, scan_added_lines, scan_patch


def test_rule_defaults_keep_conftest_and_library_rules_active(monkeypatch):
    rule = _rule("c1_probe", r"needle")
    monkeypatch.setattr(scan, "RULES", [*scan.RULES, rule])

    assert scan_added_lines("tests/conftest.py", [(1, "needle")]) == [
        Finding("tests/conftest.py", 1, "c1_probe")
    ]
    assert scan_added_lines("src/module.py", [(1, "needle")]) == [
        Finding("src/module.py", 1, "c1_probe")
    ]


def test_committed_encoding_cookie_is_checked_only_in_first_two_lines(repo):
    (repo / "src").mkdir(exist_ok=True)
    (repo / "src" / "module.py").write_text("first = 1\nsecond = 2\n# coding: latin-1\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    base = git(repo, "rev-parse", "HEAD")

    head = commit_change(repo, {"src/module.py": "first = 3\nsecond = 2\n# coding: latin-1\n"})
    assert scan_patch(repo, base, head, python=sys.executable) == []
