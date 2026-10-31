"""tamper_scan: how the diff is cut into added lines, and a few more spellings of the same APIs.

A diff line ends only at "\\n"; file headers are read only between hunks (inside a hunk the
header's line counts say how many lines follow). Python also ends a source line at a carriage
return, so an added line is scanned piece by piece at each "\\r".
"""

import pytest
from conftest import git

from cairnwork.gitcmd import GitCommandError
from cairnwork.tamper_scan import added_lines, scan_added_lines, scan_patch

TEST = "tests/test_x.py"
LIBRARY = "pkg/module.py"
BOTH = pytest.mark.parametrize("path", [TEST, LIBRARY])


def _scan(repo, content: bytes):
    base = git(repo, "rev-parse", "HEAD")
    (repo / "src" / "a.py").write_bytes(content)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "candidate")
    head = git(repo, "rev-parse", "HEAD")
    return [(f.path, f.line, f.rule) for f in scan_patch(repo, base, head)]


@pytest.mark.parametrize(
    "content",
    [
        b'NOTE = """\n++ see below\n"""\nimport os\nos._exit(0)\n',
        b'NOTE = """\n++ b/README.md\n"""\nimport os\nos._exit(0)\n',
        b'NOTE = """\n++ b/src/b.py\n@@ -1 +1 @@\n"""\nimport os\nos._exit(0)\n',
    ],
)
def test_an_added_line_that_looks_like_a_file_header_is_still_an_added_line(repo, content):
    found = _scan(repo, content)
    assert found == [("src/a.py", content.count(b"\n"), "os_exit")]


def test_added_lines_reads_headers_only_between_hunks():
    diff = (
        "diff --git a/src/a.py b/src/a.py\n--- a/src/a.py\n+++ b/src/a.py\n"
        "@@ -1,0 +2,3 @@\n+++ b/other.py\n+--- a/x\n+os._exit(0)\n"
        "diff --git a/src/b.py b/src/b.py\n--- a/src/b.py\n+++ b/src/b.py\n"
        "@@ -1 +1 @@\n-x = 1\n+y = 2\n\\ No newline at end of file\n"
    )
    assert added_lines(diff) == {
        "src/a.py": [(2, "++ b/other.py"), (3, "--- a/x"), (4, "os._exit(0)")],
        "src/b.py": [(1, "y = 2")],
    }


@pytest.mark.parametrize(
    "content",
    [
        b"import os\nx = 1\ros._exit(0)\n",  # Python ends a source line at a lone CR too
        b"import os\n# note\ros._exit(0)\n",  # a comment, then code after the CR
        b"import os\npass;\x0cos._exit(0)\n",
        'import os\ns = " "; os._exit(0)\n'.encode(),
        'import os\ns = " "; os._exit(0)\n'.encode(),
        'import os\ns = "\x85"; os._exit(0)\n'.encode(),
        b"import os\ns = '\x1c\x1d\x1e'; os._exit(0)\n",
    ],
)
def test_a_line_separator_inside_an_added_line_does_not_cut_it_short(repo, content):
    assert _scan(repo, content) == [("src/a.py", 2, "os_exit")]


def test_a_file_with_crlf_line_ends_is_scanned_with_the_right_line_numbers(repo):
    assert _scan(repo, b"x = 1\r\ny = 2\r\nos._exit(0)\r\n") == [("src/a.py", 3, "os_exit")]


def test_a_diff_that_is_not_utf8_is_still_unreadable(repo):
    with pytest.raises(GitCommandError):
        _scan(repo, b"s = '\xe9'\n")


@BOTH
@pytest.mark.parametrize(
    ("line", "rule"),
    [
        ("import posix; posix._exit(0)", "os_exit"),
        ("from posix import _exit", "os_exit"),
        ("import posix as p; p._exit(0)", "os_exit"),
        ("from gc import get_objects; get_objects()", "gc_walk"),
        ("from gc import collect, get_referrers as r", "gc_walk"),
        ("import gc as g; g.get_referrers(x)", "gc_walk"),
        ("import json, gc as g; g.get_objects()", "gc_walk"),
        ('sys.modules |= {"pytest": fake}', "sys_modules_write"),
        ('sys.modules.__setitem__("pytest", fake)', "sys_modules_write"),
        ('sys.modules.__delitem__("pytest")', "sys_modules_write"),
        ('sys.modules["tests.conftest"] = fake', "sys_modules_write"),
        ('sys.modules["tests.test_protected"] = fake', "sys_modules_write"),
        ("sys.modules['test_protected'] = fake", "sys_modules_write"),
        ("sys.modules['pkg.helpers_test'] = fake", "sys_modules_write"),
        ("del sys.modules['tests']", "sys_modules_write"),
    ],
)
def test_more_spellings_of_the_same_apis_are_caught(path, line, rule):
    assert [f.rule for f in scan_added_lines(path, [(1, line)])] == [rule]


@BOTH
@pytest.mark.parametrize(
    "line",
    [
        "import gc as g; g.collect()",
        "from gc import collect, disable",
        "import posix; posix.getpid()",
        "sys.modules['dep'] = MagicMock()",
        "sys.modules['latest_api'] = MagicMock()",  # "test" only inside a word
        "sys.modules['mytests.helper'] = MagicMock()",
    ],
)
def test_lookalikes_of_the_new_spellings_pass(path, line):
    assert scan_added_lines(path, [(1, line)]) == []


def test_operator_diff_settings_do_not_change_how_the_diff_is_read(repo):
    """A blank context line between hunks, written as "" under diff.suppressBlankEmpty."""
    git(repo, "config", "diff.suppressBlankEmpty", "true")
    git(repo, "config", "diff.interHunkContext", "5")
    (repo / "README.md").write_text("a\n\nb\n\nc\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "readme")
    base = git(repo, "rev-parse", "HEAD")
    (repo / "README.md").write_text("A\n\nb\n\nC\n")
    (repo / "src" / "z.py").write_text("import os\nos._exit(0)\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "candidate")
    head = git(repo, "rev-parse", "HEAD")
    assert [(f.path, f.line, f.rule) for f in scan_patch(repo, base, head)] == [
        ("src/z.py", 2, "os_exit")
    ]


HEAD_A = "diff --git a/README.md b/README.md\n--- a/README.md\n+++ b/README.md\n"
HEAD_Z = "diff --git a/z.py b/z.py\nnew file mode 100644\n--- /dev/null\n+++ b/z.py\n"


def test_an_empty_line_inside_a_hunk_is_a_blank_context_line():
    diff = HEAD_A + "@@ -1,5 +1,5 @@\n-a\n+A\n\n b\n\n-c\n+C\n" + HEAD_Z + "@@ -0,0 +1 @@\n+x\n"
    assert added_lines(diff) == {"README.md": [(1, "A"), (5, "C")], "z.py": [(1, "x")]}


@pytest.mark.parametrize(
    "diff",
    [
        HEAD_A + "@@ -1,2 +1,2 @@\n-a\n+A\n" + HEAD_Z + "@@ -0,0 +1 @@\n+x\n",  # counts left
        HEAD_A + "@@ -1 +1 @@\n-a\n+A\n+B\n",  # more lines than counted: B is outside the hunk
        HEAD_A + "@@ -1 +1,2 @@\n-a\n+A\n",  # the diff ends inside a hunk
        HEAD_A + "@@ -1 +1 @@\n-a\n \n+A\n",  # a context line the old side does not owe
    ],
)
def test_a_hunk_whose_lines_do_not_match_its_counts_is_unreadable(diff):
    with pytest.raises(GitCommandError):
        added_lines(diff)
