"""Survivor pins for tamper scan piece, git-read, and stand-in-file behavior."""

import sys

import pytest
from conftest import git

import cairnwork.tamper_scan as scan
import cairnwork.tamper_scan_budget as budget_module
from cairnwork.tamper_scan import scan_added_lines, scan_patch
from cairnwork.tamper_scan_budget import SCAN_BUDGET
from cairnwork.tamper_scan_tokens import added_file


def test_piece_budget_exhaustion_stops_before_later_cr_pieces(monkeypatch):
    real_budget = budget_module.ScanBudget

    class OnePieceBudget(real_budget):
        def __init__(self):
            super().__init__()
            self.pieces = 0

        def piece(self):
            self.pieces += 1
            return self.pieces == 1

    monkeypatch.setattr(scan, "ScanBudget", OnePieceBudget)
    lines = [(1, "import os\ros._exit(0)\ros._exit(1)")]
    found = scan_added_lines("src/module.py", lines)
    assert [(item.line, item.rule) for item in found] == [(1, SCAN_BUDGET)]


def test_cr_followed_by_coding_cookie_piece_reports_exact_findings():
    found = scan_added_lines(
        "src/module.py", [(1, "x = 1\r# coding: latin-1\rimport os; os._exit(0)")]
    )
    assert [(item.line, item.rule) for item in found] == [
        (1, "source_encoding"),
        (1, "os_exit"),
    ]


def _base_and_candidate(repo, content: str) -> tuple[str, str]:
    for key, value in (("user.email", "t@example.org"), ("user.name", "t")):
        git(repo, "config", key, value)
    (repo / "a.py").write_text("x = 1\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    base = git(repo, "rev-parse", "HEAD")
    (repo / "a.py").write_text(content)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "candidate")
    return base, git(repo, "rev-parse", "HEAD")


def test_oversized_diff_output_becomes_scan_budget(repo, monkeypatch):
    base, head = _base_and_candidate(repo, "x = 2\ny = 3\nz = 4\n")
    monkeypatch.setattr(scan, "MAX_GIT_READ", 8)
    found = scan_patch(repo, base, head, python=sys.executable)
    assert [(item.path, item.line, item.rule) for item in found] == [("", 0, SCAN_BUDGET)]


def test_budget_over_after_a_piece_stops_before_later_pieces(monkeypatch):
    real_rule_names = scan._rule_names  # pyright: ignore[reportPrivateUsage]

    def over_after_first(*args, **kwargs):
        names = real_rule_names(*args, **kwargs)
        args[-1].over = True
        return names

    monkeypatch.setattr(scan, "_rule_names", over_after_first)
    found = scan_added_lines("src/module.py", [(1, "x = 1\ros._exit(0)"), (2, "os._exit(1)")])
    assert [item for item in found if item.rule == "os_exit"] == []


def test_preexisting_over_budget_skips_blob_read_and_file_rules(repo, monkeypatch):
    base, head = _base_and_candidate(repo, "x = 2\n")
    calls = []
    real_run_git_bytes = scan.run_git_bytes

    def git_spy(root, *args, **kwargs):
        calls.append(args)
        return real_run_git_bytes(root, *args, **kwargs)

    class AlreadyOverBudget(budget_module.ScanBudget):
        def __init__(self):
            super().__init__()
            self.over = True

    def file_rules_spy(*args, **kwargs):
        pytest.fail("file rules must be bypassed when the scan budget is over")

    monkeypatch.setattr(scan, "run_git_bytes", git_spy)
    monkeypatch.setattr(scan, "ScanBudget", AlreadyOverBudget)
    monkeypatch.setattr(scan, "file_rules", file_rules_spy)
    assert scan_patch(repo, base, head, python=sys.executable) == []
    assert [args[0] for args in calls if args and args[0] == "cat-file"] == []


def test_added_file_empty_and_nonempty_values_are_exact():
    assert added_file([]) is None
    assert added_file([(2, "value = 1")]) == b"\nvalue = 1"


def test_added_file_rejects_line_numbers_below_one():
    assert added_file([(0, "value = 1")]) is None
    assert added_file([(3, "a"), (-1, "b")]) is None
