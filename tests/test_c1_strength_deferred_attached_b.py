"""Focused mutation pins for deferred attached name stores and callable values."""

import ast
from typing import Any

import pytest

from cairnwork.deferred_attached import _bindings, _callable, _name_stores


def _first(source: str) -> Any:
    first: Any = ast.parse(source).body[0]
    return first


def _names(source: str, plain: frozenset[str] = frozenset()) -> set[str]:
    tree = ast.parse(source)
    return {name for name, _ in _name_stores(tree, plain)}


def test_comprehension_walk_preserves_plain_scope_and_shared_context():
    generator = _first(
        "[(test_result := factory()) for item in (test_iter := factory()) "
        "if (test_filter := factory())]"
    ).value.generators[0]
    found = _name_stores(generator, frozenset({"factory"}))
    assert {name for name, _ in found} == {"test_iter", "test_filter"}


def test_comprehension_walk_keeps_function_global_scope_and_shared_names():
    function = _first(
        "def helper():\n"
        "    global test_iter_global, test_filter_global\n"
        "    values = [0 for _ in factory() if "
        "(test_filter_global := factory()) and (test_filter_local := factory())]\n"
    )
    generator = function.body[1].value.generators[0]
    generator.iter = ast.NamedExpr(
        target=ast.Name(id="test_iter_global", ctx=ast.Store()),
        value=ast.Call(func=ast.Name(id="factory", ctx=ast.Load()), args=[], keywords=[]),
    )
    local_generator = _first("[0 for _ in factory()]").value.generators[0]
    local_generator.iter = ast.NamedExpr(
        target=ast.Name(id="test_iter_local", ctx=ast.Store()),
        value=ast.Call(func=ast.Name(id="factory", ctx=ast.Load()), args=[], keywords=[]),
    )
    function.body.append(
        ast.Expr(value=ast.ListComp(elt=ast.Constant(0), generators=[local_generator]))
    )
    found = _name_stores(function, frozenset({"factory"}))
    assert {name for name, _ in found} == {"test_iter_global", "test_filter_global"}


@pytest.mark.parametrize(
    "source,expected",
    [
        (
            (
                "def helper():\n    global test_first, test_second\n"
                "    test_first = helper\n    test_second = helper\n"
            ),
            {"test_first", "test_second"},
        ),
        (
            "def helper():\n    global test_first\n    test_first = helper\n"
            "    def nested():\n        global test_hidden\n    class Nested:\n        pass\n",
            {"test_first"},
        ),
        (
            "def helper():\n    global test_first\n    test_first = helper\n"
            "    class TestNested:\n        test_local = helper\n",
            {"test_first", "test_local"},
        ),
    ],
)
def test_name_stores_respects_function_and_nested_class_scope(source, expected):
    function = ast.parse(source).body[0]
    assert {name for name, _ in _name_stores(function, frozenset({"helper"}))} == expected


def test_nested_global_function_and_class_definitions_are_attached():
    function = ast.parse(
        "def helper():\n"
        "    global test_function, TestClass\n"
        "    def test_function(): pass\n"
        "    class TestClass: pass\n"
    ).body[0]
    found = _name_stores(function, frozenset({"helper"}))
    assert {name for name, _ in found} == {"test_function", "TestClass"}
    assert all(
        isinstance(value, ast.Name) and value.id == name and isinstance(value.ctx, ast.Load)
        for name, value in found
    )


def test_nested_local_test_function_is_not_treated_as_a_global_attachment():
    function = ast.parse("def helper():\n    def test_local(): pass\n").body[0]
    assert _name_stores(function, frozenset({"helper"})) == []


def test_function_globals_scan_past_nested_scope_nodes():
    function = ast.parse(
        "def helper():\n    def nested(): pass\n    global test_later\n    test_later = helper\n"
    ).body[0]
    assert {name for name, _ in _name_stores(function, frozenset({"helper"}))} == {"test_later"}


def test_function_child_walk_preserves_global_filtering():
    function = ast.parse(
        "def helper():\n"
        "    global test_global\n"
        "    if flag:\n"
        "        test_global = factory()\n"
        "        test_local = factory()\n"
    ).body[0]
    assert {name for name, _ in _name_stores(function, frozenset({"factory"}))} == {"test_global"}


def test_name_stores_includes_class_and_module_callable_bindings():
    source = (
        "def helper(): pass\n"
        "class Outer:\n    test_alpha = helper\n"
        "    class Inner:\n        TestBeta = helper\n"
        "test_gamma = helper\n"
    )
    assert _names(source, frozenset({"helper"})) == {
        "test_alpha",
        "TestBeta",
        "test_gamma",
    }


def test_class_body_callable_call_keeps_plain_name_context():
    source = "def factory(): pass\nclass Outer:\n    test_callable = factory()\n"
    assert _names(source, frozenset({"factory"})) == {"test_callable"}


def test_match_binding_targets_are_store_names():
    match = _first("match helper:\n    case test_capture: pass\n")
    bindings = _bindings(match)
    assert len(bindings) == 1
    targets, value = bindings[0]
    assert isinstance(value, ast.Name) and value.id == "helper"
    assert [
        (getattr(target, "id", None), type(getattr(target, "ctx", None))) for target in targets
    ] == [("test_capture", ast.Store)]


def test_non_callable_binding_is_skipped_without_stopping_later_bindings():
    tree = ast.parse("test_data = 42\ntest_callable = helper\n")
    assert {name for name, _ in _name_stores(tree, frozenset({"helper"}))} == {"test_callable"}


def test_non_callable_with_item_does_not_hide_a_later_callable_item():
    with_node = ast.parse("with 0 as test_data, factory() as test_callable:\n    pass\n").body[0]
    assert {name for name, _ in _name_stores(with_node, frozenset({"factory"}))} == {
        "test_callable"
    }


def test_tuple_binding_skips_non_name_targets_and_keeps_test_name():
    tree = ast.parse("obj.attr, test_callable = helper, helper\n")
    assert {name for name, _ in _name_stores(tree, frozenset({"helper"}))} == {"test_callable"}


def test_nested_function_match_capture_requires_global_declaration():
    function = ast.parse(
        "def helper():\n"
        "    global test_capture\n"
        "    match factory():\n"
        "        case test_capture: pass\n"
    ).body[0]
    assert {name for name, _ in _name_stores(function, frozenset({"factory"}))} == {"test_capture"}


@pytest.mark.parametrize(
    "expression,plain,expected",
    [
        ("helper", {"helper"}, True),
        ("obj.member", set(), True),
        ("lambda: 1", set(), True),
        ("factory()", {"factory"}, True),
        ("42()", set(), True),
        ("factory()", set(), False),
        ("(lambda: 1) if flag else 0", set(), True),
        ("obj.member()", set(), False),
        ("factory()", {"factory", "other"}, True),
    ],
)
def test_callable_classifies_names_calls_lambdas_and_composites(expression, plain, expected):
    value = ast.parse(expression, mode="eval").body
    assert _callable(value, frozenset(plain)) is expected
