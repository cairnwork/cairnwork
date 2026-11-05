"""#821 L1: a hook name written out as a string, scan cost of the file rules, newer syntax."""
# ruff: noqa: E501

import random
import re
import sys
import time

import pytest
from conftest import commit_change, git

import cairnwork.tamper_scan as tamper_scan
from cairnwork.tamper_scan import _hits, scan_patch
from cairnwork.tamper_scan_budget import MAX_READ, ScanBudget
from cairnwork.tamper_scan_plugin import HOOK_NAMES, file_rules, setattr_forge
from cairnwork.tamper_scan_walk import hook_names_bound

LIB = "src/myplug.py"
FN = "def _f(pyfuncitem):\n    return True\n"


def _patch(repo, head, base=None):
    commit_change(
        repo, base or {LIB: "import pytest\n\n@pytest.fixture\ndef thing():\n    return 1\n"}
    )
    base_sha = git(repo, "rev-parse", "HEAD")
    sha = commit_change(repo, head)
    return {(f.path, f.rule) for f in scan_patch(repo, base_sha, sha, python=sys.executable)}


PLUG = "import pytest\n\n@pytest.fixture\ndef thing():\n    return 1\n"
FILES = {  # the plugin file after the change, a clean base
    "globals_key": FN + "globals()['pytest_pyfunc_call'] = _f\n",
    "vars_key": FN + "vars()['pytest_pyfunc_call'] = _f\n",
    "update_same_line": FN + "globals().update({'pytest_pyfunc_call': _f})\n",
    "update_next_line": FN + "HOOKS = {'pytest_pyfunc_call': _f}\nglobals().update(HOOKS)\n",
    "setattr_module": "import sys\n"
    + FN
    + "setattr(sys.modules[__name__], 'pytest_pyfunc_call', _f)\n",
    "name_in_variable": "import sys\nN = 'pytest_pyfunc_call'\n"
    + FN
    + "setattr(sys.modules[__name__], N, _f)\n",
    "implicit_concat": FN + "globals()['pytest_pyfunc' '_call'] = _f\n",
    "module_getattr": "_N = 'pytest_pyfunc_call'\ndef __dir__():\n    return [_N]\ndef __getattr__(n):\n    return _f\n",
    "loop_over_dict": "for _k, _v in {'pytest_pyfunc_call': lambda i: True}.items():\n    globals()[_k] = _v\n",
}


@pytest.mark.parametrize("body", FILES.values(), ids=FILES.keys())
def test_a_hook_name_as_a_plain_string_makes_the_file_touched(repo, body):
    assert (LIB, "hook_file_touched") in _patch(repo, {LIB: PLUG + body})


def test_a_changed_helper_that_writes_a_hook_name_is_touched_too(repo):
    base = {LIB: PLUG + "from helper import *\n", "src/helper.py": "X = 1\n"}
    body = "X = 1\nfor _n in ['pytest_pyfunc_call']:\n    globals()[_n] = lambda i: True\n"
    assert ("src/helper.py", "hook_file_touched") in _patch(repo, {"src/helper.py": body}, base)


def test_a_log_text_that_only_holds_a_name_is_not_touched(repo):
    body = "import logging\nlogging.info('pytest_configure ran')\nTEXT = 'see pytest_runtest_call docs'\n"
    assert _patch(repo, {LIB: PLUG + body}) == set()


# A plain SyntaxError under the verifier's Python: no finding, a note (tamper_scan_verifier_parse).
UNKNOWN_SYNTAX = ["print 'x'\n", "x = 1 |> f\n", "def f(:\n    pass\n"]


@pytest.mark.parametrize("body", UNKNOWN_SYNTAX)
def test_a_file_this_python_cannot_parse_is_not_touched_without_a_hook_name(repo, body):
    compile_error = pytest.raises(SyntaxError, compile, body, "x", "exec")
    assert compile_error
    assert _patch(repo, {LIB: PLUG + body}) == set()


@pytest.mark.parametrize("body", UNKNOWN_SYNTAX)
def test_a_file_the_verifier_cannot_parse_is_not_touched_by_a_hook_name_in_text(repo, body):
    """No text fallback (#848b): a name in a comment of an unparseable file is not a binding."""
    assert _patch(repo, {LIB: PLUG + body + "# pytest_pyfunc_call\n"}) == set()


# Scan cost: the parsing is paid from the patch's budget.
def test_parsing_is_paid_from_the_budget_and_runs_out_as_scan_budget(repo):
    commit_change(repo, {LIB: "X = 1\n"})
    base = git(repo, "rev-parse", "HEAD")
    head = commit_change(repo, {LIB: "X = 2\n" * 100})
    assert file_rules(repo, base, head, ScanBudget(tokenized=50), python=sys.executable) == [
        (LIB, "scan_budget")
    ]
    assert file_rules(repo, base, head, ScanBudget(), python=sys.executable) == []


def test_a_patch_already_over_budget_runs_no_file_rules(repo, monkeypatch):
    def refused(*args):
        raise AssertionError("file_rules ran on a patch that is over budget")

    monkeypatch.setattr(tamper_scan, "file_rules", refused)
    line = ("value = alpha + beta  " * 200)[:4000] + "\n"
    per_file = MAX_READ * 6 // 10 // (4 * len(line)) * line
    base = git(repo, "rev-parse", "HEAD")
    head = commit_change(repo, {"src/a.py": per_file, "src/b.py": per_file})
    assert {f.rule for f in scan_patch(repo, base, head, python=sys.executable)} == {"scan_budget"}


def test_blobs_of_many_files_are_read_by_one_cat_file(repo, monkeypatch):
    calls = []
    real = tamper_scan.file_rules.__globals__["run_git_bytes"]

    def spy(repo_, *args, **kw):
        calls.append(args[0:2])
        return real(repo_, *args, **kw)

    monkeypatch.setitem(tamper_scan.file_rules.__globals__, "run_git_bytes", spy)
    base = git(repo, "rev-parse", "HEAD")
    head = commit_change(repo, {f"src/m{i}.py": f"x{i} = {i}\n" for i in range(30)})
    assert scan_patch(repo, base, head, python=sys.executable) == []
    assert [c for c in calls if c[0] == "cat-file"] == [("cat-file", "--batch")]


# --- setattr(... '__code__') in one linear pass (#848 round 1) ---
_OLD_SETATTR = re.compile(r"""\bsetattr\s*\([^#;]*['"]__(?:code|globals)__['"]""")


def _forge(line):
    return any(_hits(r, line) for r in tamper_scan.RULES if r.name == "code_object_forge")


@pytest.mark.parametrize("gap", [0, 1, 200, 201, 5000])
@pytest.mark.parametrize("attr", ["__code__", "__globals__"])
def test_setattr_forge_far_gap_is_refused(gap, attr):
    assert _forge("setattr(f, " + "x" * gap + f" '{attr}', g)")
    assert setattr_forge("setattr(f," + " " * gap + f'"{attr}", g)')


@pytest.mark.parametrize("sep", ["#", ";"])
def test_setattr_forge_separator_is_not_refused(sep):
    assert not setattr_forge(f"setattr(f, 1 {sep} '__code__')")
    assert not setattr_forge(f"setattr(f {sep} " + "x" * 300 + " '__code__')")
    assert setattr_forge(f"setattr(f {sep} 1); setattr(f, '__code__', g)")  # a later setattr


def test_setattr_forge_reports_code_object_forge_with_line():
    repo_line = "setattr(f, " + " " * 300 + "'__code__', g)"
    assert [f.rule for f in tamper_scan.scan_added_lines("src/a.py", [(7, repo_line)])] == [
        "code_object_forge"
    ]


def test_setattr_forge_many_setattr_one_line_is_fast():
    line = "setattr(" * 20000
    start = time.perf_counter()
    assert not setattr_forge(line)
    assert setattr_forge(line + " '__code__'")
    assert time.perf_counter() - start < 1


def test_setattr_forge_agrees_with_old_pattern():
    rng = random.Random(848)
    parts = ["setattr(", "setattr (", " ", "x", "#", ";", "'__code__'", '"__globals__"', "__code__"]
    for _ in range(2000):
        line = "".join(rng.choice(parts) for _ in range(rng.randint(1, 14)))
        assert setattr_forge(line) == bool(_OLD_SETATTR.search(line)), line


@pytest.mark.parametrize(
    "line",
    [
        "setattr(" + " " * 240 + "g, '__code__', h.__code__)",
        "setattr(" + "(g if True else g)" * 12 + ", '__code__', h.__code__)",
    ],
)
def test_setattr_forge_long_first_argument_is_refused(line):
    assert setattr_forge(line)
    assert _forge(line)


def test_setattr_forge_a_separator_resets_and_a_literal_is_needed(monkeypatch):
    """The two parts of the scan each matter: the reset on ``#``/``;`` and the literal hit."""
    assert not setattr_forge("setattr(f); g('__code__')")  # reset kills the first setattr
    assert not setattr_forge("setattr(f, x)")  # no literal, no hit
    assert not setattr_forge("'__code__'")  # a literal with no setattr before it


@pytest.mark.parametrize(
    "source",
    [
        "f(pytest_pyfunc_call=1)\n",
        "match 1:\n    case pytest_pyfunc_call:\n        pass\n",
        "x = b'pytest_pyfunc_call'\n",
        "match {}:\n    case {**pytest_pyfunc_call}:\n        pass\n",
    ],
)
def test_hook_names_bound_reads_keyword_match_and_bytes_forms(source):
    assert hook_names_bound(source.encode(), HOOK_NAMES) == {"pytest_pyfunc_call"}
