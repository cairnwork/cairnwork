"""Third-batch pins for tamper_scan: cookie positions, Rule fields, scan_patch plumbing."""

import re
import sys

import pytest
from conftest import commit_change, git

import cairnwork.tamper_scan as scan
from cairnwork.gitcmd import GitCommandError
from cairnwork.gitcmd_capped import GitOutputTooLarge
from cairnwork.tamper_scan import Finding, Rule, _rule, scan_added_lines, scan_patch

COOKIE = "# coding: latin-1"
PATH = "pkg/mod.py"


def _enc(path, found):
    return [f.line for f in found if f.rule == "source_encoding"]


def test_added_cookie_only_counts_on_lines_one_and_two():
    for number, expected in ((1, [1]), (2, [2]), (3, []), (4, [])):
        found = scan_added_lines(PATH, [(number, COOKIE)])
        assert _enc(PATH, found) == expected


def test_unchanged_cookie_on_line_one_is_found_at_line_one():
    data = (COOKIE + "\nx = 1\ny = 2\n").encode()
    found = scan_added_lines(PATH, [(3, "y = 2")], data)
    assert found == [Finding(PATH, 1, "source_encoding")]


def test_unchanged_cookie_on_line_two_is_found_at_line_two():
    data = ("x = 1\n" + COOKIE + "\ny = 2\n").encode()
    found = scan_added_lines(PATH, [(3, "y = 2")], data)
    assert found == [Finding(PATH, 2, "source_encoding")]


def test_unchanged_cookie_on_line_three_is_ignored():
    data = ("a = 1\nb = 1\n" + COOKIE + "\ny = 2\n").encode()
    assert scan_added_lines(PATH, [(4, "y = 2")], data) == []


def test_unchanged_cookie_with_carriage_return_piece_is_found():
    data = ("x = 1\r" + COOKIE + "\nz = 1\ny = 2\n").encode()
    found = scan_added_lines(PATH, [(3, "y = 2")], data)
    assert found == [Finding(PATH, 1, "source_encoding")]


def test_changed_cookie_line_is_reported_once_not_twice():
    data = (COOKIE + "\nx = 1\n").encode()
    found = scan_added_lines(PATH, [(1, COOKIE)], data)
    assert found == [Finding(PATH, 1, "source_encoding")]


def test_both_cookie_lines_reported_in_order():
    data = (COOKIE + "\n" + COOKIE + "\nq = 1\n").encode()
    found = scan_added_lines(PATH, [(3, "q = 1")], data)
    assert found == [Finding(PATH, 1, "source_encoding"), Finding(PATH, 2, "source_encoding")]


def test_committed_cookie_without_data_is_not_read():
    assert scan._committed_coding(PATH, None, []) == []


def test_rule_dataclass_defaults():
    rule = Rule("n", re.compile("x"))
    assert rule.skip_in_conftest is False
    assert rule.allow is None
    assert rule.tests_only is False


def _with_rule(monkeypatch, **flags):
    monkeypatch.setattr(scan, "RULES", [*scan.RULES, _rule("c1_flag", r"needle", **flags)])


def _hit(path):
    return [f.rule for f in scan_added_lines(path, [(1, "needle")]) if f.rule == "c1_flag"]


def test_skip_in_conftest_flag_is_not_tests_only(monkeypatch):
    _with_rule(monkeypatch, skip_in_conftest=True, tests_only=False)
    assert _hit("tests/conftest.py") == []
    assert _hit("tests/test_x.py") == ["c1_flag"]
    assert _hit("src/lib.py") == ["c1_flag"]


def test_tests_only_flag_is_not_skip_in_conftest(monkeypatch):
    _with_rule(monkeypatch, skip_in_conftest=False, tests_only=True)
    assert _hit("tests/conftest.py") == ["c1_flag"]
    assert _hit("tests/test_x.py") == ["c1_flag"]
    assert _hit("src/lib.py") == []


def test_allow_pattern_is_third_field_and_skips_whole_line(monkeypatch):
    _with_rule(monkeypatch, allow=r"^needle$")
    assert _hit("src/lib.py") == []
    assert [f.rule for f in scan_added_lines("src/lib.py", [(1, "needle + 1")])] == ["c1_flag"]


@pytest.fixture
def two_commits(repo):
    base = git(repo, "rev-parse", "HEAD")
    head = commit_change(repo, {"src/a.py": "x = 2\n"})
    return repo, base, head


def test_diff_command_line_is_exact(monkeypatch, two_commits):
    repo, base, head = two_commits
    calls = []

    def fake(r, *args, **kw):
        calls.append((r, args, kw))
        return b""

    monkeypatch.setattr(scan, "run_git_bytes", fake)
    assert scan_patch(repo, base, head, python=sys.executable) == []
    r, args, kw = calls[0]
    assert r == repo
    assert args == (
        "-c", "diff.suppressBlankEmpty=false", "-c", "diff.interHunkContext=0",
        "diff", "--no-ext-diff", "--no-textconv", "--no-color", "--no-renames", "-U0", "--text",
        "--full-index", "--src-prefix=a/", "--dst-prefix=b/", base, head, "--", "*.py",
    )  # fmt: skip
    assert kw == {"max_bytes": scan.MAX_GIT_READ}


def test_diff_too_large_is_refused_with_empty_path(monkeypatch, two_commits):
    repo, base, head = two_commits

    def fake(*a, **k):
        raise GitOutputTooLarge("big")

    monkeypatch.setattr(scan, "run_git_bytes", fake)
    assert scan_patch(repo, base, head, python=sys.executable) == [Finding("", 0, "scan_budget")]


def test_non_utf8_diff_raises_with_cause(monkeypatch, two_commits):
    repo, base, head = two_commits
    monkeypatch.setattr(scan, "run_git_bytes", lambda *a, **k: b"\xff\xfe")
    with pytest.raises(GitCommandError, match=r"git diff output is not UTF-8$") as info:
        scan_patch(repo, base, head, python=sys.executable)
    assert isinstance(info.value.__cause__, UnicodeDecodeError)


@pytest.mark.parametrize("head", ["HEAD", "abc123", "main", "-x"])
def test_head_must_be_full_commit_id(monkeypatch, two_commits, head):
    repo, base, _ = two_commits
    monkeypatch.setattr(scan, "run_git_bytes", lambda *a, **k: b"")
    monkeypatch.setattr(scan, "file_rules", lambda *a: pytest.fail("checked too late"))
    with pytest.raises(GitCommandError) as info:
        scan_patch(repo, base, head, python=sys.executable)
    assert str(info.value) == "GIT_COMMAND_FAILED: head is not a full commit id"


def test_blob_too_large_returns_findings_so_far_then_budget_at_that_path(monkeypatch, repo):
    base = git(repo, "rev-parse", "HEAD")
    head = commit_change(
        repo, {"src/a.py": "import os\nos._exit(1)\n", "src/b.py": "y = 2\n", "src/c.py": "z=1\n"}
    )
    real = scan.run_git_bytes

    def fake(r, *args, **kw):
        if args[:2] == ("cat-file", "blob") and args[2].endswith("src/b.py"):
            raise GitOutputTooLarge("big")
        return real(r, *args, **kw)

    monkeypatch.setattr(scan, "run_git_bytes", fake)
    found = scan_patch(repo, base, head, python=sys.executable)
    assert found[-1] == Finding("src/b.py", 0, "scan_budget")
    assert Finding("src/a.py", 2, "os_exit") in found[:-1]
    assert not any(f.path == "src/c.py" for f in found)


def test_blob_is_read_at_head_with_cat_file(monkeypatch, two_commits):
    repo, base, head = two_commits
    real = scan.run_git_bytes
    seen = []

    def fake(r, *args, **kw):
        if args[0] == "cat-file":
            seen.append((r, args, kw))
        return real(r, *args, **kw)

    monkeypatch.setattr(scan, "run_git_bytes", fake)
    scan_patch(repo, base, head, python=sys.executable)
    assert seen == [
        (repo, ("cat-file", "blob", f"{head}:src/a.py"), {"max_bytes": scan.MAX_GIT_READ})
    ]


def test_file_rules_findings_are_appended_with_line_zero(monkeypatch, two_commits):
    repo, base, head = two_commits
    notes = []
    got = []

    def fake_rules(*args):
        got.append(args)
        return [("p1.py", "r_one"), ("p2.py", "r_two")]

    monkeypatch.setattr(scan, "file_rules", fake_rules)
    found = scan_patch(repo, base, head, python=sys.executable, notes=notes)
    assert found == [Finding("p1.py", 0, "r_one"), Finding("p2.py", 0, "r_two")]
    r, b, h, budget, py, n = got[0]
    assert (r, b, h, py) == (repo, base, head, sys.executable)
    assert n is notes
    assert budget.over is False


def test_file_rules_not_run_when_budget_over(monkeypatch, two_commits):
    repo, base, head = two_commits

    class Over(scan.ScanBudget):
        def piece(self):
            self.over = True
            return True

    monkeypatch.setattr(scan, "ScanBudget", Over)
    monkeypatch.setattr(
        scan, "file_rules", lambda *a: pytest.fail("file_rules must not run past the budget")
    )
    found = scan_patch(repo, base, head, python=sys.executable)
    assert found == [Finding("src/a.py", 1, "scan_budget")]


def test_test_file_rule_failure_falls_back_to_no_custom_rule(monkeypatch, repo):
    base = git(repo, "rev-parse", "HEAD")
    head = commit_change(repo, {"checks/probe.py": "import os\nos._exit(1)\n"})

    def boom(*a):
        raise GitCommandError("no config")

    monkeypatch.setattr(scan, "test_file_rule", boom)
    monkeypatch.setattr(scan, "RULES", [*scan.RULES, _rule("c1_t", r"needle", tests_only=True)])
    commit = commit_change(repo, {"checks/probe.py": "needle\n"})
    found = scan_patch(repo, head, commit, python=sys.executable)
    assert [f.rule for f in found] == []
    assert not any(f.rule == "c1_t" for f in scan_patch(repo, base, head, python=sys.executable))


def test_test_file_rule_result_is_passed_to_scan(monkeypatch, repo):
    base = git(repo, "rev-parse", "HEAD")
    head = commit_change(repo, {"checks/probe.py": "x = 1\n"})
    marker = lambda p: True  # noqa: E731
    monkeypatch.setattr(scan, "test_file_rule", lambda r, b: marker)
    seen = []
    real = scan.scan_added_lines

    def spy(path, lines, data, budget, trusted, is_test):
        seen.append(is_test)
        return real(path, lines, data, budget, trusted, is_test)

    monkeypatch.setattr(scan, "scan_added_lines", spy)
    scan_patch(repo, base, head, python=sys.executable)
    assert seen == [marker]


def test_files_after_a_budget_overrun_are_not_read(monkeypatch, repo):
    base = git(repo, "rev-parse", "HEAD")
    head = commit_change(repo, {f"src/m{i}.py": f"v{i} = 1\n" for i in range(6)})

    class Over(scan.ScanBudget):
        def piece(self):
            self.over = True
            return True

    reads = []
    real = scan.run_git_bytes

    def spy(r, *args, **kw):
        if args[0] == "cat-file":
            reads.append(args[2])
        return real(r, *args, **kw)

    monkeypatch.setattr(scan, "ScanBudget", Over)
    monkeypatch.setattr(scan, "run_git_bytes", spy)
    found = scan_patch(repo, base, head, python=sys.executable)
    assert len(reads) == 1
    assert [f.rule for f in found] == ["scan_budget"]


def test_piece_budget_exhaustion_keeps_earlier_findings_and_current_line(monkeypatch):
    class Two(scan.ScanBudget):
        calls = 0

        def piece(self):
            Two.calls += 1
            return Two.calls <= 2

    monkeypatch.setattr(scan, "ScanBudget", Two)
    lines = [(5, "os._exit(1)"), (6, "os._exit(2)"), (9, "z = 1"), (10, "z = 2")]
    found = scan_added_lines("src/lib.py", lines)
    assert found == [
        Finding("src/lib.py", 5, "os_exit"),
        Finding("src/lib.py", 6, "os_exit"),
        Finding("src/lib.py", 9, "scan_budget"),
    ]


def test_long_cookie_line_gets_both_long_line_and_unchanged_cookie_finding():
    long_first = COOKIE + " " * (scan.MAX_LINE + 1)
    data = (long_first + "\nx = 1\n").encode()
    found = scan_added_lines(PATH, [(1, long_first)], data)
    assert found == [Finding(PATH, 1, "long_line"), Finding(PATH, 1, "source_encoding")]
