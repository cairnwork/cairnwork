"""#821 L1: pytest hooks, internals and code-object forgery in any candidate ``.py``.

Each group below has several spellings of one class, and lookalikes that must pass. A bare ``scan``
reads added lines of one file; the ``git`` tests go through ``scan_patch`` (whole files, binaries).
"""
# ruff: noqa: E501  (source samples are one-line strings)

import sys

import pytest
from conftest import commit_change, git

from cairnwork.tamper_scan import scan_added_lines, scan_patch
from cairnwork.tamper_scan_plugin import HOOK_NAMES
from cairnwork.tamper_scan_walk import hook_names_bound

LIB = "src/myplug.py"


def scan(source, path=LIB):
    lines = list(enumerate(source.split("\n"), start=1))
    return {finding.rule for finding in scan_added_lines(path, lines)}


# ---- AC1: hook implementations in a library file ----
AC1 = {
    "hookimpl_attr": "import pytest\n@pytest.hookimpl(hookwrapper=True)\ndef pytest_runtest_makereport(item, call):\n    yield",
    "wrapper_true": "import pytest\n@pytest.hookimpl(wrapper=True)\ndef pytest_runtest_call(item):\n    return (yield)",
    "tryfirst": "import pytest\n@pytest.hookimpl(tryfirst=True)\ndef pytest_pyfunc_call(pyfuncitem):\n    return True",
    "from_import": "from pytest import hookimpl\n@hookimpl(tryfirst=True)\ndef pytest_pyfunc_call(pyfuncitem):\n    return True",
    "alias_import": "import pytest as pt\n@pt.hookimpl(wrapper=True)\ndef g(item):\n    return (yield)",
    "bare_def": "def pytest_pyfunc_call(pyfuncitem):\n    return True",
}


@pytest.mark.parametrize("source", AC1.values(), ids=AC1.keys())
def test_ac1_a_hook_in_a_library_file_is_refused(source):
    assert "pytest_hook_impl" in scan(source)


def test_ac1_the_same_hook_in_a_conftest_is_still_normal_and_a_real_fix_is_clean():
    assert scan(AC1["bare_def"], "tests/conftest.py") == set()
    assert scan("def add(a, b):\n    return a + b") == set()


# ---- AC2: pytest internals ----
AC2 = {
    "assign": "def pytest_configure(config):\n    config.pluginmanager._inner_hookexec = hx",
    "alias_assign": "pm = config.pluginmanager\npm._inner_hookexec = hx",
    "setattr": 'setattr(config.pluginmanager, "_inner_hookexec", hx)',
    "vars": 'vars(config.pluginmanager)["_inner_hookexec"] = hx',
}


@pytest.mark.parametrize("source", AC2.values(), ids=AC2.keys())
def test_ac2_replacing_the_hook_execution_entry_is_refused(source):
    assert "pytest_internals" in scan(source)
    if "def " in source:
        assert "pytest_hook_impl" in scan(source)


# ---- AC3: a hook built from a code object ----
AC3 = {
    "types": (
        "pytest_pyfunc_call = types.FunctionType(f.__code__, {})",
        {"pytest_hook_impl", "code_object_forge"},
    ),
    "alias": (
        "from types import FunctionType as F; pytest_pyfunc_call = F(f.__code__, g)",
        {"pytest_hook_impl"},
    ),
    "type_of_lambda": ("pytest_pyfunc_call = type(lambda: 0)(f.__code__, g)", {"pytest_hook_impl"}),
    "code_assign": ("f.__code__ = other.__code__", {"code_object_forge"}),
    "replace": ("c = f.__code__.replace(co_filename='x.py')", {"code_object_forge"}),
    "setattr": ("setattr(f, '__code__', other.__code__)", {"code_object_forge"}),
    "globals": ("f.__globals__['x'] = 1", {"code_object_forge"}),
}


@pytest.mark.parametrize("source,rules", AC3.values(), ids=AC3.keys())
def test_ac3_forging_a_function_from_a_code_object_is_refused(source, rules):
    assert rules <= scan(source)


# ---- AC5: compile ----
@pytest.mark.parametrize(
    "source",
    [
        'code = compile(src, "src/calc.py", "exec")',
        'c = builtins.compile(s, "x", "exec")',
        "c = compile\nv = c(s, 'x', 'exec')",
        "run = compile ; run(s)",
        "x = compile(s, f, 'eval')",
    ],
)
def test_ac5_the_builtin_compile_is_refused(source):
    assert "code_object_forge" in scan(source)


@pytest.mark.parametrize(
    "source",
    [
        "p = re.compile(r'a+')",
        "exec(code)",
        "eval(expr)",
        "def compile(self, tree):\n    return tree",
        "q = self.compile(tree)",
    ],
)
def test_ac5_re_compile_exec_eval_and_a_method_named_compile_pass(source):
    assert "code_object_forge" not in scan(source)


# ---- AC6: pluggy and pytest internals ----
AC6 = [
    "item.ihook.pytest_runtest_call(item=i)",
    "session.hook.pytest_collection_finish(session=s)",
    "config.hook.pytest_runtest_call(item=i)",
    "pm.add_hookcall_monitoring(before, after)",
    "pm.enable_tracing()",
    "import pluggy",
    "from pluggy import HookimplMarker",
    "x = HookCaller(name, hm)",
    "y = HookImpl(plugin, f, opts)",
    'z = getattr(item, "ihook")',
]


@pytest.mark.parametrize("source", AC6)
def test_ac6_pluggy_and_hook_call_internals_are_refused(source):
    assert "pytest_internals" in scan(source)


@pytest.mark.parametrize(
    "source",
    [
        "x = BuildBackendHookCaller()",
        "fihook = 1",
        "webhook.send(payload)",
        "self.hooked = True",
        "from _pytest.fixtures import FixtureRequest  # type hint",
    ],
)
def test_ac6_lookalike_names_pass(source):
    assert "pytest_internals" not in scan(source)


# ---- option writes ----
@pytest.mark.parametrize(
    "source",
    [
        "config.option.maxfail = 99",
        'setattr(config.option, "exitfirst", False)',
        "vars(config.option)['verbose'] = 0",
        "config.option.keyword += 'x'",
        "config.option.__dict__.update(o)",
    ],
)
def test_writing_config_option_is_refused(source):
    assert "pytest_option_write" in scan(source)


@pytest.mark.parametrize(
    "source",
    [
        "n = config.option.maxfail",
        "if config.option.verbose == 1:\n    pass",
        "print(getattr(config.option, 'x', None))",
    ],
)
def test_reading_config_option_passes(source):
    assert "pytest_option_write" not in scan(source)


# ---- AC9: honest code ----
@pytest.mark.parametrize(
    "source",
    [
        "ok = isinstance(x, types.FunctionType)",
        "name = f.__code__.co_filename",
        "if f.__code__.co_filename == path:\n    pass",
        "n = len(f.__code__.co_names)",
    ],
)
def test_ac9_reading_a_function_and_checking_its_type_pass(source):
    assert scan(source) == set()


# ---- AC22: dynamic loaders ----
AC22 = [
    "m = importlib.machinery.SourceFileLoader('x', p).load_module()",
    "s = importlib.util.spec_from_file_location('x', p)",
    "d = runpy.run_path(p)",
    "d = runpy.run_module('x')",
    "from importlib.util import spec_from_file_location as s",
    "from runpy import run_path",
]


@pytest.mark.parametrize("source", AC22)
def test_ac22_loading_source_from_a_path_is_refused(source):
    assert "dynamic_loader" in scan(source)


def test_ac22_importing_by_name_passes():
    assert scan('m = importlib.import_module("json")') == set()


# ---- AC23: every group above has enough spellings ----
def test_ac23_each_group_has_at_least_three_spellings():
    groups = {"AC1": AC1, "AC2": AC2, "AC3": AC3, "AC6": AC6, "AC22": AC22}
    assert all(len(group) >= 3 for group in groups.values())


# ---- AC7 and AC8: whole files, through a real commit ----
def _patch(repo, files, base_files=None):
    if base_files:
        commit_change(repo, base_files)
    base = git(repo, "rev-parse", "HEAD")
    head = commit_change(repo, files)
    return {(f.path, f.rule) for f in scan_patch(repo, base, head, python=sys.executable)}


@pytest.mark.parametrize(
    "name",
    ["a.pyc", "a.pyo", "a.so", "a.pyd", "a.dylib", "a.zip", "a.egg", "a.whl", "a.pyz", "A.SO"],
)
def test_ac7_a_sourceless_file_in_the_patch_is_refused(repo, name):
    assert _patch(repo, {f"src/{name}": "x"}) == {(f"src/{name}", "sourceless_binary")}


def test_ac7_changing_a_binary_the_base_has_is_refused_and_leaving_it_alone_is_not(repo):
    found = _patch(repo, {"src/ext.so": "two"}, {"src/ext.so": "one"})
    assert found == {("src/ext.so", "sourceless_binary")}
    assert _patch(repo, {"src/a.py": "x = 2\n"}) == set()


def test_ac7_deleting_a_binary_and_data_files_are_not_refused(repo):
    commit_change(repo, {"src/ext.so": "one", "src/data.json": "{}"})
    base = git(repo, "rev-parse", "HEAD")
    head = commit_change(repo, {"src/data.json": "[]"}, delete=("src/ext.so",))
    assert scan_patch(repo, base, head, python=sys.executable) == []


HOOK_BASE = "def helper():\n    return 1\n\ndef pytest_addoption(parser):\n    pass\n"
TOUCHED = {
    "unchanged_hook_other_edit": "def helper():\n    return 2\n\ndef pytest_addoption(parser):\n    pass\n",
    "hook_deleted": "def helper():\n    return 1\n",
    "class_method": "class P:\n    def pytest_runtest_call(self, item):\n        pass\n",
    "def_in_if": "if True:\n    def pytest_pyfunc_call(pyfuncitem):\n        return True\n",
    "def_in_try": "try:\n    def pytest_pyfunc_call(pyfuncitem):\n        return True\nexcept Exception:\n    pass\n",
    "def_in_with": "with open('x') as fh:\n    async def pytest_sessionstart(session):\n        pass\n",
    "import_as": "from helpers import f as pytest_pyfunc_call\n",
    "plain_import": "from helpers import pytest_configure\n",
    "annotated": "from typing import Callable\npytest_pyfunc_call: Callable = f\n",
    "augmented": "pytest_plugins += ['x']\n",
    "for_target": "for pytest_pyfunc_call in fs:\n    pass\n",
    "with_target": "with ctx() as pytest_sessionfinish:\n    pass\n",
    "attribute": "plugin.pytest_runtest_call = f\n",
}


@pytest.mark.parametrize("head", TOUCHED.values(), ids=TOUCHED.keys())
def test_ac8_a_changed_file_that_binds_a_hook_name_is_refused_unread(repo, head):
    assert _patch(repo, {LIB: head}, {LIB: HOOK_BASE}) >= {(LIB, "hook_file_touched")}


@pytest.mark.parametrize(
    "head",
    [v for k, v in TOUCHED.items() if k not in ("unchanged_hook_other_edit", "hook_deleted")],
    ids=[k for k in TOUCHED if k not in ("unchanged_hook_other_edit", "hook_deleted")],
)
def test_ac8_a_head_that_binds_a_hook_with_a_clean_base_is_refused_too(repo, head):
    """Each head-side shape on its own: nothing in the base to find (mutant M1 of the review)."""
    assert _patch(repo, {LIB: head}, {LIB: "X = 1\n"}) >= {(LIB, "hook_file_touched")}


@pytest.mark.parametrize(
    "head",
    [
        "def pytest_my_helper():\n    return 1\n",
        "X = 2\n",
        "def test_x():\n    pass\n",
        "pytest_my_value = 3\n",
    ],
)
def test_ac8_other_names_and_files_without_hook_names_do_not_trigger(repo, head):
    assert _patch(repo, {LIB: head}, {LIB: "def pytest_my_helper():\n    return 0\n"}) == set()


def test_ac8_the_finding_names_the_file_without_a_line_number(repo):
    commit_change(repo, {LIB: HOOK_BASE})
    base = git(repo, "rev-parse", "HEAD")
    head = commit_change(repo, {LIB: HOOK_BASE + "Y = 1\n"})
    assert [str(f) for f in scan_patch(repo, base, head, python=sys.executable)] == [
        f"{LIB} [hook_file_touched]"
    ]


def test_ac8_a_deleted_hook_file_is_refused(repo):
    commit_change(repo, {LIB: HOOK_BASE})
    base = git(repo, "rev-parse", "HEAD")
    head = commit_change(repo, delete=(LIB,))
    assert {(f.path, f.rule) for f in scan_patch(repo, base, head, python=sys.executable)} == {
        (LIB, "hook_file_touched")
    }


def test_the_hook_name_set_is_pytests_own_plus_two_names():
    import _pytest.hookspec as spec

    own = {name for name in vars(spec) if name.startswith("pytest_")}
    assert own <= HOOK_NAMES and HOOK_NAMES - own == {"pytest_plugins"}
    assert hook_names_bound(b"def pytest_addoption(p):\n    pass\n", HOOK_NAMES) == {
        "pytest_addoption"
    }
