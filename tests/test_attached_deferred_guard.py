"""#1194: direct ``refuse_attached`` tests. The commit-level path runs hidden_tests (#925) first,
which masks module-level binds. Covers builtin attribute escapes, match captures and a
comprehension walrus. Tighten-only: everything the base guard refused stays refused."""

import ast

import pytest

from cairnwork.deferred_attached import _name_stores, _stores, refuse_attached
from cairnwork.deferred_tests import TestCommitRejected as Rejected

F = "tests/test_regression.py"


ESCAPES = {
    "len-self-module": "test_x = len.__self__.eval('lambda: 1')\n",
    "len-self-class": "class TestX:\n    test_x = len.__self__.eval('lambda self: None')\n",
    "helper-call": (
        "class H:\n    @staticmethod\n    def make():\n        return lambda: 1\n"
        "test_x = H.make()\n"
    ),
    "builtins-eval-module": "import builtins\ntest_x = builtins.eval('lambda: 1')\n",
    "builtins-eval-class": (
        "import builtins\nclass TestX:\n    test_x = builtins.eval('lambda self: None')\n"
    ),
}


@pytest.mark.parametrize("shape", sorted(ESCAPES))
@pytest.mark.parametrize("before_kind", ("empty", "changed"))
def test_attribute_escape_is_refused_by_the_attached_guard(shape, before_kind):
    after = ESCAPES[shape]
    before = "test_old = lambda: 1\n" if before_kind == "changed" else ""
    with pytest.raises(Rejected) as err:
        refuse_attached(F, ast.parse(after), ast.parse(before))
    assert err.value.code == "DEFERRED_TESTS_UNREADABLE"


def test_a_local_match_capture_is_not_an_attaching_store():
    source = (
        "def helper(value):\n    match value:\n        case test_mode:\n"
        "            pass\nhelper(1)\n"
    )
    refuse_attached(F, ast.parse(source), ast.parse(source))
    match = ast.parse("match value:\n    case test_mode:\n        pass\n").body[0]
    assert _stores(match) == []


def test_function_global_match_capture_is_an_attaching_store():
    source = (
        "def helper(value):\n    global test_mode\n"
        "    match value:\n        case test_mode:\n            pass\n"
        "helper(len)\n"
    )
    with pytest.raises(Rejected):
        refuse_attached(F, ast.parse(source), ast.parse(""))


def test_comprehension_filter_walrus_is_a_name_store():
    source = ast.parse("def helper(): pass\n[0 for _ in [0] if (test_x := helper)]")
    plain = frozenset({"helper"})
    assert any(name == "test_x" for name, _ in _name_stores(source.body[1], plain))


def test_class_body_match_capture_is_an_attaching_store():
    source = "class TestX:\n    match len:\n        case test_mode:\n            pass\n"
    with pytest.raises(Rejected):
        refuse_attached(F, ast.parse(source), ast.parse(""))


def test_a_builtin_method_forbidden_escape_is_an_unreadable_attachment():
    source = "class TestX:\n    pass\nTestX.test_x = len.__self__.eval('lambda self: None')\n"
    with pytest.raises(Rejected) as err:
        refuse_attached(F, ast.parse(source), ast.parse(source))
    assert err.value.code == "DEFERRED_TESTS_UNREADABLE"


def test_qualified_builtins_eval_is_an_unreadable_attachment():
    source = (
        "class TestX:\n    pass\nimport builtins\n"
        "TestX.test_x = builtins.eval('lambda self: None')\n"
    )
    with pytest.raises(Rejected) as err:
        refuse_attached(F, ast.parse(source), ast.parse(source))
    assert err.value.code == "DEFERRED_TESTS_UNREADABLE"


def test_getattr_reading_an_attribute_does_not_make_a_test_attachment():
    source = "class Helper:\n    value = 1\nresult = getattr(Helper, 'value', None)\n"
    refuse_attached(F, ast.parse(source), ast.parse(source))


@pytest.mark.parametrize(
    "value", ("dict.get({}, 'a')", "str.join(',', ['a'])", "eval.__call__('1')")
)
def test_a_builtin_rooted_attribute_call_in_an_unchanged_file_is_unreadable(value):
    source = f"test_x = {value}\n"
    with pytest.raises(Rejected) as err:
        refuse_attached(F, ast.parse(source), ast.parse(source))
    assert err.value.code == "DEFERRED_TESTS_UNREADABLE"
