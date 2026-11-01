"""tamper_scan reads comments and strings as Python does (tamper_scan_tokens.py, #571).

Whether a ``#`` starts a comment, or a ``;`` ends a statement, depends on the lines before it: a
string literal opened earlier (an unchanged line included) holds both as plain text. Each case
first checks with ``ast`` that Python compiles the statement the rule is about.
"""

import ast
import unicodedata

import pytest
from conftest import git

from cairnwork.gitcmd import GitCommandError
from cairnwork.tamper_scan import scan_added_lines, scan_patch
from cairnwork.tamper_scan_tokens import file_views

TEST = "tests/test_x.py"
LIBRARY = "pkg/module.py"


def _rules(path: str, source: str) -> set[str]:
    lines = list(enumerate(source.split("\n")[:-1], start=1))
    return {finding.rule for finding in scan_added_lines(path, lines)}


def _compiles(source: str, statement: str) -> bool:
    return statement in ast.unparse(ast.parse(source)).split("\n")


CODE_AFTER_A_STRING = [  # (path, source, rule, a statement Python compiles from it)
    (LIBRARY, 'x = """\n# """; os._exit(0)\n', "os_exit", "os._exit(0)"),
    (LIBRARY, 'x = "abc\\\n# "; os._exit(0)\n', "os_exit", "os._exit(0)"),
    (LIBRARY, 'x = """\n# """; gc.get_objects()\n', "gc_walk", "gc.get_objects()"),
    (
        LIBRARY,
        'x = """\n# """; pytest.Item.runtest = None\n',
        "pytest_public_patch",
        "pytest.Item.runtest = None",
    ),
    (
        TEST,
        "sys.modules['dep'] = '#'; sys.modules['pytest'] = None\n",
        "sys_modules_write",
        "sys.modules['pytest'] = None",
    ),
    (
        TEST,
        'x = """\nfrom _pytest.fixtures import FixtureRequest  # """; import _pytest.python as m\n',
        "pytest_internals",
        "import _pytest.python as m",
    ),
    (
        TEST,
        "x = '''\nsys.modules['dep'] = None  # '''; sys.modules['pytest'] = None\n",
        "sys_modules_write",
        "sys.modules['pytest'] = None",
    ),
    (
        LIBRARY,
        "pytest.Item.runtest: '#' = None\n",
        "pytest_public_patch",
        "pytest.Item.runtest: '#' = None",
    ),
    (
        LIBRARY,
        "pytest.TestReport.passed: ';' = True\n",
        "pytest_public_patch",
        "pytest.TestReport.passed: ';' = True",
    ),
    (
        LIBRARY,
        "del d['#'], pytest.TestReport.when\n",
        "pytest_public_patch",
        "del d['#'], pytest.TestReport.when",
    ),
    (
        LIBRARY,
        "del d[';'], pytest.TestReport.when\n",
        "pytest_public_patch",
        "del d[';'], pytest.TestReport.when",
    ),
    (
        LIBRARY,
        "del d['#'], sys.modules['pytest']\n",
        "sys_modules_write",
        "del d['#'], sys.modules['pytest']",
    ),
]


@pytest.mark.parametrize(("path", "source", "rule", "statement"), CODE_AFTER_A_STRING)
def test_a_hash_or_semicolon_inside_a_string_is_text_not_syntax(path, source, rule, statement):
    assert _compiles(source, statement)
    assert rule in _rules(path, source)


@pytest.mark.parametrize(
    ("path", "source"),
    [
        (LIBRARY, "# os._exit(0) is not called here\n"),
        (LIBRARY, 'x = """text"""\n    # gc.get_objects() in a comment\n'),
        (TEST, "sys.modules['dep'] = MagicMock()  # a stub; see #221\n"),
        (TEST, "sys.modules['dep'] = '#'  # the stub's value holds a hash\n"),
        (TEST, "from _pytest.fixtures import FixtureRequest  # type hint only\n"),
        (LIBRARY, "isinstance(r, pytest.TestReport)  # '#'; ';'\n"),
        (LIBRARY, "label = '# not a comment; not a separator'\n"),
        (LIBRARY, 'DOC = """\n# os._exit(0) in an example\n"""\n'),
        (LIBRARY, "del d['k']; print(pytest.TestReport.when)\n"),
    ],
)
def test_real_comments_and_honest_strings_still_pass(path, source):
    assert _rules(path, source) == set()


def test_a_line_that_starts_inside_an_unchanged_string_is_read_with_the_whole_file(repo):
    (repo / "src" / "a.py").write_text('NOTE = """\nkept\n"""\n')
    git(repo, "commit", "-qam", "base")
    base = git(repo, "rev-parse", "HEAD")
    head_text = 'NOTE = """\nkept\n# """; os._exit(0); END = """\n"""\n'
    assert _compiles(head_text, "os._exit(0)")
    (repo / "src" / "a.py").write_text(head_text)
    git(repo, "commit", "-qam", "candidate")
    head = git(repo, "rev-parse", "HEAD")
    assert ("src/a.py", 3, "os_exit") in {
        (f.path, f.line, f.rule) for f in scan_patch(repo, base, head)
    }


def test_code_inside_an_f_string_line_is_not_skipped():
    source = 'x = f"""\n#{os._exit(0)}\n"""\n'
    calls = [node for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Call)]
    assert [ast.unparse(node) for node in calls] == ["os._exit(0)"]
    assert "os_exit" in _rules(LIBRARY, source)


@pytest.mark.parametrize("cookie", [b"# coding: utf-7\n", b"# coding: unicode_escape\n"])
def test_an_encoding_declared_on_an_unchanged_first_line_is_refused(repo, cookie):
    (repo / "src" / "a.py").write_bytes(cookie + b"import os\n")
    git(repo, "commit", "-qam", "base")
    base = git(repo, "rev-parse", "HEAD")
    (repo / "src" / "a.py").write_bytes(cookie + b"import os\nx = 1\n")
    git(repo, "commit", "-qam", "candidate")
    head = git(repo, "rev-parse", "HEAD")
    assert ("src/a.py", 1, "source_encoding") in {
        (f.path, f.line, f.rule) for f in scan_patch(repo, base, head)
    }


def test_a_file_python_cannot_tokenize_skips_no_comment_line():
    assert file_views(b'x = """\n') is None
    assert file_views(b"\xff\n") is None
    lines = [(1, 'x = """'), (2, "# os._exit(0)")]
    assert {f.rule for f in scan_added_lines(LIBRARY, lines)} == {"os_exit"}


def test_views_follow_carriage_returns_and_a_byte_order_mark():
    views = file_views(b'\xef\xbb\xbfx = "#"\r\n# note\ry = 1\n')
    assert views is not None
    assert views[(1, 0)].code == 'x = "x"'
    assert views[(2, 0)].skip
    assert views[(2, 1)].code == "y = 1"
    assert (1, 1) not in views  # "\r\n" ends one line, not two


def test_an_encoding_declaration_moved_up_to_line_2_is_refused(repo):
    (repo / "src" / "a.py").write_bytes(
        b"#!/usr/bin/env python\nX = 1\n# vim: fileencoding=utf-7\n"
    )
    git(repo, "commit", "-qam", "base")
    base = git(repo, "rev-parse", "HEAD")
    (repo / "src" / "a.py").write_bytes(
        b"#!/usr/bin/env python\n# vim: fileencoding=utf-7\nY = 2\n"
    )
    git(repo, "commit", "-qam", "candidate")
    head = git(repo, "rev-parse", "HEAD")
    assert ("src/a.py", 2, "source_encoding") in {
        (f.path, f.line, f.rule) for f in scan_patch(repo, base, head)
    }


def test_a_crlf_file_keeps_its_comment_lines_after_a_continued_line():
    lines = [(1, "x = 1 + \\\r"), (2, "    2\r"), (3, "# os._exit(0) is not called\r")]
    assert scan_added_lines(LIBRARY, lines) == []


@pytest.mark.parametrize("path", [TEST, LIBRARY])
@pytest.mark.parametrize(
    ("source", "rule"),
    [
        ('import os as o\n"""\nfrom sys import foo #, modules as os\n"""\no._exit(0)\n', "os_exit"),
        (
            'import gc as g\n"""\nfrom sys import foo #, modules as gc\n"""\ng.get_objects()\n',
            "gc_walk",
        ),
        (
            "from sys import modules as m\n'''\nfrom sys import foo #, modules as sys\n'''\n"
            "m['pytest'] = 1\n",
            "sys_modules_write",
        ),
    ],
)
def test_an_import_inside_a_string_literal_leaves_later_aliases_as_they_were(path, source, rule):
    assert rule in _rules(path, source)


def test_a_tokenizer_error_of_any_type_gives_no_views(monkeypatch):
    def fail(readline):
        raise SystemError("tokenizer")

    monkeypatch.setattr("cairnwork.tamper_scan_tokens.tokenize.generate_tokens", fail)
    assert file_views(b"x = 1\n") is None
    assert {f.rule for f in scan_added_lines(LIBRARY, [(1, "# os._exit(0)")])} == {"os_exit"}


def test_scan_patch_takes_only_a_full_commit_id(repo):
    with pytest.raises(GitCommandError):
        scan_patch(repo, git(repo, "rev-parse", "HEAD"), "HEAD")


@pytest.mark.parametrize("path", [TEST, LIBRARY])
@pytest.mark.parametrize(
    ("source", "rule"),
    [
        ('import os as o\n"""\nfrom sys import modules as os\n"""\no._exit(0)\n', "os_exit"),
        ('import gc as g\n"""\nfrom sys import modules as gc\n"""\ng.get_objects()\n', "gc_walk"),
        (
            "from sys import modules as m\n'''\nfrom sys import modules as sys\n'''\n"
            "m['pytest'] = 1\n",
            "sys_modules_write",
        ),
        ("import os as o\nx = 1  # ; from sys import modules as os\no._exit(0)\n", "os_exit"),
        ("import os as o\ny = 'note: from sys import modules as os, x'\no._exit(0)\n", "os_exit"),
        ('import ｏｓ as o\n"""\nfrom sys import modules as os\n"""\no._exit(0)\n', "os_exit"),
        ('import os as o\n"""\nfrom sys import modules as os\n"""\nｏ._exit(0)\n', "os_exit"),
    ],
)
def test_an_alias_bound_only_inside_a_string_or_comment_does_not_hide_a_later_call(
    path, source, rule
):
    call = unicodedata.normalize("NFKC", source.rstrip("\n").split("\n")[-1])
    assert _compiles(source, call)
    assert rule in _rules(path, source)


@pytest.mark.parametrize("path", [TEST, LIBRARY])
@pytest.mark.parametrize(
    ("bind", "name", "call", "rule"),
    [
        ("import os as o", "os", "o._exit(0)", "os_exit"),
        ("import gc as g", "gc", "g.get_objects()", "gc_walk"),
    ],
)
def test_an_alias_bound_only_inside_a_string_does_not_hide_a_call_after_it_closes(
    path, bind, name, call, rule
):
    text = "from sys import modules as " + name
    source = bind + '\nx = """\n' + text + '\n# """; ' + call + "\n"
    assert _compiles(source, call)
    assert rule in _rules(path, source)


def test_an_alias_bound_by_code_after_a_string_is_followed():
    source = 'x = """\n""" ; import os as o\no._exit(0)\n'
    assert "os_exit" in _rules(LIBRARY, source)
