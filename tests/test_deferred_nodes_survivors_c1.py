"""Regression probes for deferred_nodes AST reader mutation survivors."""

from __future__ import annotations

import ast

from cairnwork.deferred_nodes import hidden_tests
from cairnwork.deferred_nodes import test_functions as read_functions


def _nodes(source: str) -> dict[str, str]:
    return read_functions(ast.parse(source))[0]


def _hidden(source: str) -> frozenset[str]:
    return hidden_tests(ast.parse(source))


def test_inherited_uncollected_flag_and_testcase_aliases_propagate():
    source = (
        "from unittest import TestCase as Base\n"
        "class Fixture(Base):\n    def __init__(self): pass\n"
        "class TestRunner(Fixture):\n    def test_run(self): pass\n"
    )
    nodes, blocked = read_functions(ast.parse(source))
    assert nodes.keys() == {"TestRunner::test_run"}
    assert blocked == frozenset({"TestRunner"})


def test_inherited_method_body_and_class_decorator_change_subclass_fingerprint():
    old = (
        "import pytest\n"
        "class TestBase:\n    def test_x(self): assert 1\n"
        "class TestChild(TestBase):\n    def test_y(self): pass\n"
    )
    new = old.replace("assert 1", "assert 2").replace(
        "class TestChild(TestBase):", "@pytest.mark.skip\nclass TestChild(TestBase):"
    )
    before, after = _nodes(old), _nodes(new)
    assert (
        before.keys()
        == after.keys()
        == {"TestBase::test_x", "TestChild::test_x", "TestChild::test_y"}
    )
    assert before["TestChild::test_x"] != after["TestChild::test_x"]
    assert before["TestChild::test_y"] != after["TestChild::test_y"]


def test_testcase_base_spelling_variants_collect_methods():
    source = (
        "class First(unittest.TestCase):\n    def test_a(self): pass\n"
        "class Second(SimpleTestCase):\n    def test_b(self): pass\n"
        "class Third(TestCase):\n    def test_c(self): pass\n"
    )
    assert _nodes(source).keys() == {"First::test_a", "Second::test_b", "Third::test_c"}


def test_nested_test_decorator_fingerprint_changes():
    old = "class TestOuter:\n    class TestInner:\n        def test_x(self): pass\n"
    new = old.replace("class TestInner:", "@mark\n    class TestInner:")
    before = _nodes(old)["TestOuter::TestInner::test_x"]
    after = _nodes(new)["TestOuter::TestInner::test_x"]
    assert before != after


def test_module_reach_tracks_all_bindings_referenced_by_test():
    old = "FLAG = 1\ndef test_x():\n    assert FLAG\n"
    new = old.replace("FLAG = 1", "FLAG = 2")
    assert _nodes(old)["test_x"] != _nodes(new)["test_x"]


def test_hidden_test_function_and_class_spellings_are_reported():
    source = (
        "if enabled:\n    def test_block(): pass\nif enabled:\n    class TestBlock:\n        pass\n"
    )
    got = _hidden(source)
    assert ast.dump(ast.parse("def test_block(): pass").body[0]) in got
    assert ast.dump(ast.parse("class TestBlock: pass").body[0]) in got


def test_hidden_targets_include_match_rest_and_dotted_import_top_name():
    source = (
        "match value:\n    case {'x': _, **TestRest}: pass\n"
        "from package.submodule import value as test_imported\n"
    )
    got = _hidden(source)
    assert len(got) == 2
    assert any("TestRest" in item for item in got)
    assert any("test_imported" in item for item in got)


def test_global_declared_test_name_marks_whole_function_hidden():
    source = "def install():\n    global TestLater\n    TestLater = make()\n"
    assert ast.dump(ast.parse(source).body[0]) in _hidden(source)


def test_call_values_are_hidden_but_literal_values_are_not():
    assert _hidden("test_value = build()\n")
    assert _hidden("test_value = {'x': (1, 2)}\n") == frozenset()


def test_literal_reader_distinguishes_constant_from_call_expression():
    # A call-valued test binding is recorded as hidden; a literal is deliberately ignored.
    assert len(_hidden("test_value = make()\n")) == 1
    assert not _hidden("test_value = None\n")


def test_unittest_hooks_and_pytest_hooks_are_module_reach():
    for name in ("setup_module", "teardown_function", "setUpModule", "pytest_generate_tests"):
        old = f"def {name}():\n    return 1\ndef test_x(): pass\n"
        new = old.replace("return 1", "return 2")
        assert _nodes(old)["test_x"] != _nodes(new)["test_x"]


def test_nested_test_class_outer_shell_changes_hidden_context():
    old = "class Outer:\n    flag = 1\n    class TestInner: pass\n"
    new = old.replace("flag = 1", "flag = 2")
    assert _hidden(old) != _hidden(new)


# equivalent: line 166 separator for a single decorator: no output change when only one item.
# equivalent: line 167 separator for one inherited method: join has no separator to add.
# equivalent: line 172 None replacements only affect nested traversal arguments where no
# nested class exists; covered by dedicated nested-class fingerprints above.
