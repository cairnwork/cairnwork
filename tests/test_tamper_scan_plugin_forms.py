"""#821 L1, review round 2: a hook name given as a plain string, scan cost, newer syntax."""
# ruff: noqa: E501

import pytest
from conftest import commit_change, git

import cairnwork.tamper_scan as tamper_scan
from cairnwork.tamper_scan import scan_added_lines, scan_patch
from cairnwork.tamper_scan_budget import MAX_READ, ScanBudget
from cairnwork.tamper_scan_plugin import file_rules

LIB = "src/myplug.py"
FN = "def _f(pyfuncitem):\n    return True\n"


def scan(source, path=LIB):
    return {f.rule for f in scan_added_lines(path, list(enumerate(source.split("\n"), 1)))}


# A name written out whole and handed to a dynamic write or lookup is read like the plain name.
LINES = {
    "globals_key": ("globals()['pytest_pyfunc_call'] = _f", "pytest_hook_impl"),
    "vars_key": ("vars()['pytest_pyfunc_call'] = _f", "pytest_hook_impl"),
    "setattr_module": (
        "setattr(sys.modules[__name__], 'pytest_pyfunc_call', _f)",
        "pytest_hook_impl",
    ),
    "getattr_compile": ("c = getattr(builtins, 'compile')", "code_object_forge"),
    "vars_builtins": ("c = vars(builtins)['comp' + 'ile']", "code_object_forge"),
    "getattr_manager": ("x = getattr(config, 'pluginmanager')", "pytest_internals"),
    "import_concat": ("m = importlib.import_module('_pyt' + 'est.runner')", "pytest_internals"),
    "import_split": ("m = importlib.import_module('plu' 'ggy')", "pytest_internals"),
    "import_plain": ("m = importlib.import_module('_pytest.runner')", "pytest_internals"),
}


@pytest.mark.parametrize("source,rule", LINES.values(), ids=LINES.keys())
def test_a_whole_name_in_a_dynamic_access_is_refused(source, rule):
    assert rule in scan(source)


@pytest.mark.parametrize(
    "source",
    [
        "m = importlib.import_module('json')",
        "getattr(obj, 'name')",
        "log.info('pytest_configure ran')",
        "globals()['handler_' + kind] = f",
        "setattr(obj, 'compiled', True)",
    ],
)
def test_other_names_in_dynamic_access_pass(source):
    assert scan(source) == set()


def _patch(repo, head, base=None):
    commit_change(
        repo, base or {LIB: "import pytest\n\n@pytest.fixture\ndef thing():\n    return 1\n"}
    )
    base_sha = git(repo, "rev-parse", "HEAD")
    sha = commit_change(repo, head)
    return {(f.path, f.rule) for f in scan_patch(repo, base_sha, sha)}


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


# Newer syntax than the scanner's Python knows: searched as text, not refused for failing to parse.
UNKNOWN_SYNTAX = ["print 'x'\n", "x = 1 |> f\n", "def f(:\n    pass\n"]


@pytest.mark.parametrize("body", UNKNOWN_SYNTAX)
def test_a_file_this_python_cannot_parse_is_not_touched_without_a_hook_name(repo, body):
    compile_error = pytest.raises(SyntaxError, compile, body, "x", "exec")
    assert compile_error
    assert _patch(repo, {LIB: PLUG + body}) == set()


@pytest.mark.parametrize("body", UNKNOWN_SYNTAX)
def test_a_file_this_python_cannot_parse_is_touched_when_a_hook_name_is_in_it(repo, body):
    assert (LIB, "hook_file_touched") in _patch(repo, {LIB: PLUG + body + "# pytest_pyfunc_call\n"})
    assert _patch(repo, {LIB: PLUG + body + "# my_pytest_pyfunc_call_x\n"}) == set()


# Scan cost: the parsing is paid from the patch's budget.
def test_parsing_is_paid_from_the_budget_and_runs_out_as_scan_budget(repo):
    commit_change(repo, {LIB: "X = 1\n"})
    base = git(repo, "rev-parse", "HEAD")
    head = commit_change(repo, {LIB: "X = 2\n" * 100})
    assert file_rules(repo, base, head, ScanBudget(tokenized=50)) == [(LIB, "scan_budget")]
    assert file_rules(repo, base, head, ScanBudget()) == []


def test_a_patch_already_over_budget_runs_no_file_rules(repo, monkeypatch):
    def refused(*args):
        raise AssertionError("file_rules ran on a patch that is over budget")

    monkeypatch.setattr(tamper_scan, "file_rules", refused)
    line = ("value = alpha + beta  " * 200)[:4000] + "\n"
    per_file = MAX_READ * 6 // 10 // (4 * len(line)) * line
    base = git(repo, "rev-parse", "HEAD")
    head = commit_change(repo, {"src/a.py": per_file, "src/b.py": per_file})
    assert {f.rule for f in scan_patch(repo, base, head)} == {"scan_budget"}


def test_blobs_of_many_files_are_read_by_one_cat_file(repo, monkeypatch):
    calls = []
    real = tamper_scan.file_rules.__globals__["run_git_bytes"]

    def spy(repo_, *args, **kw):
        calls.append(args[0:2])
        return real(repo_, *args, **kw)

    monkeypatch.setitem(tamper_scan.file_rules.__globals__, "run_git_bytes", spy)
    base = git(repo, "rev-parse", "HEAD")
    head = commit_change(repo, {f"src/m{i}.py": f"x{i} = {i}\n" for i in range(30)})
    assert scan_patch(repo, base, head) == []
    assert [c for c in calls if c[0] == "cat-file"] == [("cat-file", "--batch")]
