"""#1768 review families: a counted file that collects no item is not always a hidden file.

A helper-only module, a mixin-only module and a class with ``__init__`` define nothing pytest
collects; an ``importorskip`` or a module-level skip is reached. None of them is refused. A hook
that empties a module with real test definitions, and ``norecursedirs``, still are.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from conftest import commit_change
from test_collect_parity_files import _case

from cairnwork.collect_parity import _counted, _dirs, _ignored, _may_have_tests, _shown
from cairnwork.verifier_spec import VerifierRejected, parse_verifier

EMPTY_HOOK = (
    "import pytest\n"
    "class _Empty(pytest.Module):\n"
    "    def collect(self):\n"
    "        return []\n"
    "@pytest.hookimpl(wrapper=True)\n"
    "def pytest_collect_file(file_path, parent):\n"
    "    result = yield\n"
    "    if file_path.name == 'test_hidden.py':\n"
    "        return [_Empty.from_parent(parent, path=file_path)]\n"
    "    return result\n"
)

NOT_TESTS = {
    "helper": "def make():\n    return 1\n",
    "mixin": "class Mixin:\n    def test_x(self):\n        pass\n",
    "init": "class TestA:\n    def __init__(self):\n        pass\n    def test_x(self):\n        pass\n",  # noqa: E501
    "plain_class": "class Helper:\n    def test_x(self):\n        pass\n",
    "dict_subclass": "class FakeResp(dict):\n    pass\n",
    "object_mixin": "class BaseMixin(object):\n    def test_x(self):\n        pass\n",
    "abc_mixin": "from abc import ABC\nclass M(ABC):\n    def test_x(self):\n        pass\n",
    "import_helper": "from _cases import helper\n",
    "plain_alias": "from _cases import helper as util\nvalue = util()\n",
    "empty": "",
    "test_off": "class TestX:\n    __test__ = False\n    def test_x(self):\n        pass\n",
}
TESTS = {
    "function": "def test_a():\n    pass\n",
    "async": "async def test_a():\n    pass\n",
    "class": "class TestA:\n    def test_x(self):\n        pass\n",
    "nested_if": "import sys\nif sys.platform:\n    def test_a():\n        pass\n",
    "based_class": "import unittest\nclass A(unittest.TestCase):\n    def test_x(self):\n        pass\n",  # noqa: E501
    "unparsable": "def test_a(:\n",
    "for_target": "for test_a in range(1):\n    pass\n",
    "with_target": "import io\nwith io.StringIO() as test_a:\n    pass\n",
    "from_import": "from _cases import test_neg\n",
    "import_alias": "from _cases import neg as test_neg\n",
    "star_import": "from _cases import *\n",
    "assigned": "def _mk():\n    return 1\ntest_neg = _mk()\n",
    "annotated": "test_neg: object = None\n",
    "class_attr": "def _f(self):\n    pass\nclass TestA:\n    test_neg = _f\n",
    "nested_class": "class TestA:\n    class TestB:\n        def test_x(self):\n            pass\n",
    "class_decorator": "def deco(c):\n    return c\n@deco\nclass Mix:\n    pass\n",
    "metaclass": "class Mix(metaclass=type):\n    pass\n",
    "unknown_base": "from _cases import Base\nclass Mix(Base):\n    pass\n",
    "dynamic_globals": "globals()['test_a'] = lambda: None\n",
    "dynamic_setattr": "import sys\nsetattr(sys.modules[__name__], 'test_a', lambda: None)\n",
    "dynamic_exec": "exec('def test_a(): pass')\n",
}


def _task(repo, source):
    base = commit_change(repo, {"tests/test_x.py": source})
    return SimpleNamespace(repo=repo, base_commit=base, test_roots=("tests",))


BOUND = {
    "walrus_if": "import _c\nif (test_neg := _c.test_neg):\n    pass\n",
    "walrus_while": "import _c\nwhile not (test_neg := _c.test_neg):\n    pass\n",
    "walrus_for": "import _c\nfor _ in [(test_neg := _c.test_neg)]:\n    pass\n",
    "walrus_with": "import _c, contextlib\nwith contextlib.nullcontext(test_neg := _c.test_neg):\n    pass\n",  # noqa: E501
    "match_import": "match 1:\n    case _:\n        from _c import test_neg\n",
    "match_def": "match 1:\n    case _:\n        def test_neg():\n            pass\n",
    "match_capture": "import _c\nmatch _c.test_neg:\n    case test_neg:\n        pass\n",
    "walrus_in_class": "def _f():\n    pass\nclass TestA:\n    (test_neg := _f)\n",
    "global_bind": "import _c\ndef _bind():\n    global test_neg\n    test_neg = _c.test_neg\n_bind()\n",  # noqa: E501
    "sys_modules_attr": "import sys, _c\nsys.modules[__name__].test_neg = _c.test_neg\n",
    "sys_modules_dict": "import sys, _c\nsys.modules[__name__].__dict__['test_neg'] = _c.test_neg\n",  # noqa: E501
    "builtins_exec": "import builtins\nbuiltins.exec('from _c import test_neg')\n",
    "builtins_globals": "import builtins, _c\nbuiltins.globals()['test_neg'] = _c.test_neg\n",
    "alias_globals": "import _c\ng = globals\ng()['test_neg'] = _c.test_neg\n",
    "rebound_list": "import unittest\nlist = unittest.TestCase\nclass Checks(list):\n    def test_neg(self):\n        pass\n",  # noqa: E501
    "rebound_object": "from unittest import TestCase as object\nclass Checks(object):\n    def test_neg(self):\n        pass\n",  # noqa: E501
    "rebound_abc": "from unittest import TestCase as ABC\nclass Checks(ABC):\n    def test_neg(self):\n        pass\n",  # noqa: E501
    "deleted_init": "class TestA:\n    def __init__(self):\n        pass\n    del __init__\n    def test_neg(self):\n        pass\n",  # noqa: E501
    "test_off_deleted": "class TestX:\n    __test__ = False\n    del __test__\n    def test_x(self):\n        pass\n",  # noqa: E501
    "test_off_rebound": "class TestX:\n    __test__ = False\n    __test__ = True\n    def test_x(self):\n        pass\n",  # noqa: E501
    "dict_update": "import sys, _c\nsys.modules[__name__].__dict__.update(test_neg=_c.test_neg)\n",  # noqa: E501
    "try_star_else": "try:\n    pass\nexcept* ValueError:\n    pass\nelse:\n    def test_neg():\n        pass\n",  # noqa: E501
    "try_star_finally": "try:\n    pass\nfinally:\n    def test_neg():\n        pass\n",
    "func_globals": "def f():\n    pass\nf.__globals__['test_a'] = lambda: None\n",
    "frame_inspect": "import inspect\ninspect.currentframe().f_globals['test_a'] = lambda: None\n",
    "frame_sys": "import sys\nsys._getframe().f_globals['test_a'] = lambda: None\n",
    "frame_back": "import sys\nsys._getframe().f_back.f_globals['test_a'] = lambda: None\n",
    "gen_frame": "def g():\n    yield 1\ng().gi_frame.f_globals['test_a'] = lambda: None\n",
    "coro_frame": "async def c():\n    pass\nc().cr_frame.f_globals['test_a'] = lambda: None\n",
    "getattr_string": "def f():\n    pass\ngetattr(f, '__globals__')['test_a'] = lambda: None\n",
    "getattr_computed": "def f():\n    pass\nname = '__glo' + 'bals__'\ngetattr(f, name)['test_a'] = lambda: None\n",  # noqa: E501
    "except_star": "try:\n    from _c import test_neg\nexcept* ValueError:\n    pass\n",
}
PLAIN = {
    "attribute_abc": "import abc\nclass M(abc.ABC):\n    def test_x(self):\n        pass\n",
    "frame_word_attributes": "def f(df, self):\n    return df.frame, self.frame_count, df.back\n",
    "getattr_plain": "def f(x):\n    return getattr(x, 'name')\n",
    "local_name": "def helper():\n    test_dir = 1\n    return test_dir\n",
}


@pytest.mark.parametrize("kind", sorted(BOUND))
def test_a_test_name_bound_by_any_statement_may_have_tests(repo, kind):
    assert _may_have_tests(_task(repo, BOUND[kind]), "tests/test_x.py") is True


@pytest.mark.parametrize("kind", sorted(PLAIN))
def test_an_attribute_abc_base_is_a_plain_base(repo, kind):
    assert _may_have_tests(_task(repo, PLAIN[kind]), "tests/test_x.py") is False


@pytest.mark.parametrize("kind", sorted(NOT_TESTS))
def test_a_module_that_defines_nothing_collectable_may_have_no_tests(repo, kind):
    assert _may_have_tests(_task(repo, NOT_TESTS[kind]), "tests/test_x.py") is False


@pytest.mark.parametrize("kind", sorted(TESTS))
def test_a_module_that_defines_tests_may_have_tests(repo, kind):
    assert _may_have_tests(_task(repo, TESTS[kind]), "tests/test_x.py") is True


@pytest.mark.parametrize(
    ("key", "source"),
    [
        ("python_functions", "def make():\n    return 1\n"),
        ("python_classes", "class Helper:\n    pass\n"),
    ],
)
def test_a_custom_naming_rule_says_there_may_be_tests(repo, key, source):
    base = commit_change(repo, {"pytest.ini": f"[pytest]\n{key} = *\n", "tests/test_x.py": source})
    task = SimpleNamespace(repo=repo, base_commit=base, test_roots=("tests",))
    assert _may_have_tests(task, "tests/test_x.py") is True


def test_a_directory_pattern_does_not_count_a_file_pytest_never_walks(repo):
    base = commit_change(
        repo,
        {
            "pytest.ini": "[pytest]\npython_files = tests/*.py\n",
            "tests/a.py": "def test_a(): pass\n",
            "other/b.py": "def test_b(): pass\n",
        },
    )
    task = SimpleNamespace(repo=repo, base_commit=base, test_roots=("tests",))
    assert _counted(task, ("tests", "other")) == ["tests/a.py"]


def test_selected_files_give_their_parent_directories():
    files = ("a/b/test_x.py", "a/test_y.py", "c/test_z.py", "test_root.py")
    assert _dirs(files) == (".", "a", "a/b", "c")


def test_a_root_test_file_is_counted_and_its_subdirectories_are_not_read(repo):
    base = commit_change(
        repo, {"test_root.py": "def test_a(): pass\n", "tests/test_b.py": "def test_b(): pass\n"}
    )
    task = SimpleNamespace(repo=repo, base_commit=base)
    assert _counted(task, (".",)) == ["test_root.py"]
    assert "tests" in _ignored(task, (".",))


@pytest.mark.parametrize(
    "name", ["a\nb.py", "a\x1b[31m.py", "a\u202eb.py", "a]; b:c.py", "a\rb.py"]
)
def test_a_shown_path_has_no_newline_control_or_record_delimiter(name):
    out = _shown(name)
    assert out.isprintable() and not set("[];:") & set(out)


def test_a_run_ignores_the_subdirectories_it_does_not_select(repo):
    base = commit_change(
        repo,
        {
            "tests/test_a.py": "def test_a(): pass\n",
            "tests/big/test_b.py": "def test_b(): pass\n",
            "tests/mid/low/test_c.py": "def test_c(): pass\n",
            "tests/mid/test_d.py": "def test_d(): pass\n",
        },
    )
    task = SimpleNamespace(repo=repo, base_commit=base)
    got = _ignored(task, ("tests", "tests/mid/low"))
    assert got == ("tests/big",)


@pytest.mark.sandbox
@pytest.mark.parametrize("kind", ["helper", "mixin", "init"])
def test_a_counted_file_with_nothing_to_collect_is_not_refused(repo, tmp_path, kind):
    result = _case(repo, tmp_path, "", {"tests/test_helpers.py": NOT_TESTS[kind]})
    assert result.status == "COMPLETED" and result.verified, result.reason


@pytest.mark.sandbox
@pytest.mark.parametrize(
    "source",
    [
        "import pytest\npytest.importorskip('no_such_mod_rv1776')\ndef test_a():\n    pass\n",
        "import pytest\npytest.skip('no', allow_module_level=True)\ndef test_a():\n    pass\n",
    ],
)
def test_a_skipped_module_is_not_a_hidden_file(repo, tmp_path, source):
    result = _case(repo, tmp_path, "", {"tests/test_optional.py": source})
    assert result.status == "COMPLETED" and result.verified, result.reason


@pytest.mark.sandbox
def test_a_hook_that_empties_a_module_with_tests_is_refused(repo, tmp_path):
    hook = EMPTY_HOOK
    result = _case(repo, tmp_path, hook, {"tests/test_hidden.py": TESTS["function"]})
    assert result.reason.startswith("P2P_BASE_FILES_UNCOLLECTED: "), result.reason


def test_a_root_test_file_cannot_be_a_check_target_so_no_run_reads_it():
    with pytest.raises(VerifierRejected):
        parse_verifier("pytest", ["python3", "-m", "pytest", "-q", "test_calc.py::test_mul"])


HIDDEN = {
    "imported": "from _cases import test_neg\n",
    "star_import": "from _cases import *\n",
    "assigned": "import _cases\ntest_neg = _cases.test_neg\n",
    "nested_class": "class TestOuter:\n    class TestInner:\n        def test_neg(self):\n            pass\n",  # noqa: E501
    "class_attr": "import _cases\nclass TestA:\n    test_neg = _cases.test_neg\n",
}
CASES = "def test_neg():\n    assert True\ndef helper():\n    return 1\n"


@pytest.mark.sandbox
@pytest.mark.parametrize("kind", sorted(HIDDEN))
def test_an_emptied_module_with_bound_tests_is_refused(repo, tmp_path, kind):
    hook = EMPTY_HOOK
    result = _case(
        repo, tmp_path, hook, {"tests/_cases.py": CASES, "tests/test_hidden.py": HIDDEN[kind]}
    )
    assert result.reason.startswith("P2P_BASE_FILES_UNCOLLECTED: "), result.reason


@pytest.mark.sandbox
@pytest.mark.parametrize("kind", ["dict_subclass", "object_mixin", "import_helper"])
def test_a_benign_helper_module_is_not_refused(repo, tmp_path, kind):
    extra = {"tests/_cases.py": CASES, "tests/test_helpers.py": NOT_TESTS[kind]}
    result = _case(repo, tmp_path, "", extra)
    assert result.status == "COMPLETED" and result.verified, result.reason
