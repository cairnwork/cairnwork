"""Focused pins for surviving deferred-attached AST analysis mutants."""

import ast
from typing import Any, cast

from cairnwork.deferred_attached import (
    _annotations,
    _bindings,
    _blind_attribute_call,
    _call_stores,
    _import_part,
    _is_self,
    _key,
    _name_stores,
    _plain,
    _stores,
    attached,
)


def _parse(source: str, mode: str = "exec") -> Any:
    return ast.parse(source, mode=mode)


def test_plain_excludes_names_bound_by_non_scope_constructs():
    tree = _parse(
        """import package.sub
def test_scope(arg):
    global shared
    try:
        pass
    except Exception as caught:
        pass
    match value:
        case capture:
            pass
        case {**rest}:
            pass
def outer():
    class Nested: pass
def helper(): pass
def rebind():
    helper = 1
"""
    )
    names = _plain(tree)
    assert not names.intersection(
        {"package", "arg", "shared", "caught", "capture", "rest", "Nested", "helper"}
    )
    assert "test_scope" in names


def test_unreached_function_import_part_keeps_all_definition_time_expressions():
    function = _parse(
        "@decorate\ndef helper(value: Param = positional, *, other: Kw = keyword) -> Result: pass"
    ).body[0]
    assert _import_part(function) == [
        function.decorator_list[0],
        function.args.defaults[0],
        function.args.kw_defaults[0],
        function.args.args[0].annotation,
        function.args.kwonlyargs[0].annotation,
        function.returns,
    ]
    assert _import_part(function, reached=True) == [function]


def test_annotations_include_return_expression():
    method = _parse("def method(value: Input) -> Output: pass").body[0]
    assert _annotations(method) == [method.args.args[0].annotation, method.returns]


def test_class_import_part_keeps_methods_reached_by_class_body_but_not_tests():
    cls = _parse(
        """class Holder:
    def helper(self, value=default): pass
    def test_generated(self): pass
    use = helper
"""
    ).body[0]
    parts = _import_part(cls, reached=True)
    assert cls.body[0] in parts
    assert cls.body[1] not in parts


def test_non_reached_class_includes_only_referenced_non_test_methods():
    cls = _parse(
        """class Holder:
    def used(self): pass
    def unused(self): pass
    def test_sample(self): pass
    value = used
"""
    ).body[0]
    parts = _import_part(cls)
    assert cls.body[0] in parts
    assert cls.body[1] not in parts
    assert cls.body[2] not in parts


def test_blind_attribute_call_distinguishes_namespace_escape_from_regular_calls():
    assert _blind_attribute_call(_parse("builtins.eval('1')", mode="eval").body)
    assert _blind_attribute_call(_parse("len.__self__.eval('1')", mode="eval").body)
    assert _blind_attribute_call(_parse("obj.__self__.eval('1')", mode="eval").body)
    assert _blind_attribute_call(_parse("obj.__globals__.get('x')()", mode="eval").body)
    assert not _blind_attribute_call(_parse("helper(1)", mode="eval").body)
    assert not _blind_attribute_call(_parse("obj.method(1)", mode="eval").body)


def test_blind_attribute_call_scans_nested_call_expressions():
    expr = _parse("wrap(plain, builtins.eval('1'))", mode="eval").body
    assert _blind_attribute_call(expr)


def test_stores_ignores_literal_and_keeps_computed_setattr_target():
    nodes = _parse("value = 1\nsetattr(Target, name, helper)\n").body
    stores = _stores(ast.Module(body=nodes, type_ignores=[]))
    assert len(stores) == 1 and stores[0][0] is None
    literal = _parse("class Cls:\n    test_x = 1\nCls.test_y = 2\n")
    assert _stores(literal) == []


def test_attached_digest_changes_for_each_static_attaching_store():
    before = _parse("def helper(): return 1\ntest_generated = helper\n")
    after = _parse("def helper(): return 2\ntest_generated = helper\n")
    assert attached(before)[1] is False
    assert attached(after)[1] is False
    assert attached(before)[0] != attached(after)[0]


def test_attached_marks_dynamic_test_name_as_unreadable():
    tree = _parse("def helper(): return 1\ntest_generated = helper\n")
    dynamic = _parse("def helper(): return 1\ntest_generated = len.__self__.eval('helper')\n")
    assert attached(tree)[1] is False
    assert attached(dynamic)[1] is True


def test_name_stores_respects_nested_global_function_and_class_bindings():
    tree = _parse(
        """def outer():
    global test_function, TestClass
    def test_function(): pass
    class TestClass: pass
"""
    )
    plain = _plain(tree)
    found = _name_stores(tree.body[0], plain)
    assert [(name, ast.dump(value)) for name, value in found] == [
        ("test_function", "Name(id='test_function', ctx=Load())"),
        ("TestClass", "Name(id='TestClass', ctx=Load())"),
    ]


def test_name_stores_ignores_function_locals_without_global_declaration():
    tree = _parse(
        """def outer():
    def test_local(): pass
    class TestLocal: pass
"""
    )
    assert _name_stores(tree.body[0], _plain(tree)) == []


def test_bindings_covers_assignment_loop_context_and_match_targets():
    nodes = _parse(
        """left = right
for item in source: pass
with manager() as resource: pass
match subject:
    case {"test_x": captured}: pass
"""
    ).body
    assert _bindings(nodes[0]) == [(nodes[0].targets, nodes[0].value)]
    assert _bindings(nodes[1]) == [([nodes[1].target], nodes[1].iter)]
    assert _bindings(nodes[2]) == [
        ([nodes[2].items[0].optional_vars], nodes[2].items[0].context_expr)
    ]
    matched = _bindings(nodes[3])
    assert len(matched) == 1 and matched[0][1] is nodes[3].subject
    assert [cast(Any, t).id for t in matched[0][0]] == ["captured"]


def test_call_stores_reads_namespace_update_setdefault_and_setitem():
    calls = _parse(
        """globals().update({"test_a": helper, "other": plain}, test_kw=callable)
globals().setdefault("test_b", helper)
globals().__setitem__("TestC", helper)
"""
    ).body
    assert _call_stores(calls[0].value) == [
        ("test_a", calls[0].value.args[0].values[0]),
        ("", calls[0].value.args[0].values[1]),
        ("test_kw", calls[0].value.keywords[0].value),
    ]
    assert _call_stores(calls[1].value) == [("test_b", calls[1].value.args[1])]
    assert _call_stores(calls[2].value) == [("TestC", calls[2].value.args[1])]


def test_call_stores_marks_computed_namespace_escape_and_ignores_builtins():
    escaped = _parse("mutator(globals())", mode="eval").body
    builtin = _parse("len(globals())", mode="eval").body
    assert _call_stores(escaped) == [(None, escaped.args[0])]
    assert _call_stores(builtin) == []


def test_test_key_and_self_target_boundaries():
    assert _key(ast.Constant(value="test_case")) == "test_case"
    assert _key(ast.Constant(value="ordinary")) == ""
    assert _key(ast.Constant(value=3)) is None
    assert _is_self(ast.Name(id="self", ctx=ast.Load()))
    assert not _is_self(ast.Name(id="other", ctx=ast.Load()))


def test_computed_namespace_calls_are_blind_and_namespaces_stay_reachable():
    assert _blind_attribute_call(_parse("globals()", mode="eval").body)
    tree = _parse("""class Holder:
    def test_generated(self): pass
    use = test_generated
    setting = helper
    attach = globals().update({"test_x": helper})
""").body[0]
    parts = _import_part(tree, reached=True)
    assert any(isinstance(part, ast.Assign) and part in tree.body for part in parts)
    assert tree.body[0] not in parts
    assert tree.body[1] in parts and tree.body[2] in parts and tree.body[3] in parts


def test_name_stores_follows_comprehension_bindings_and_match_captures():
    comp = _parse(
        "values = [test_item := helper for item in source if (test_flag := helper)]"
    ).body[0]
    plain = _plain(ast.Module(body=[_parse("def helper(): pass").body[0], comp], type_ignores=[]))
    found = _name_stores(comp, plain)
    assert {name for name, _ in found} == {"test_item", "test_flag"}

    match = _parse("match helper():\n    case test_capture: pass").body[0]
    assert any(name == "test_capture" for name, _ in _name_stores(match, frozenset({"helper"})))


def test_name_stores_keeps_later_tuple_target_and_skips_noncallable_values():
    tuple_assignment = _parse("ordinary, test_generated = helper, helper").body[0]
    assert any(
        name == "test_generated"
        for name, _ in _name_stores(tuple_assignment, frozenset({"helper"}))
    )
    literal_assignment = _parse("test_data = 42").body[0]
    assert _name_stores(literal_assignment, frozenset()) == []


def test_call_stores_preserves_mapping_and_keyword_updates_and_flags_unknown_mapping():
    update = _parse(
        'globals().update({"test_a": helper}, test_b=helper, ordinary=plain)', mode="eval"
    ).body
    stores = _call_stores(update)
    assert [name for name, _ in stores] == ["test_a", "test_b", ""]
    unknown = _parse("globals().update(other_mapping)", mode="eval").body
    assert _call_stores(unknown) == [(None, unknown.args[0])]


def test_name_stores_scans_all_with_items_after_an_unreadable_context_manager():
    tree = _parse(
        """from elsewhere import external_context
def local_context(): pass
with external_context() as ordinary, local_context() as test_generated:
    pass
"""
    )
    plain = _plain(tree)
    assert any(name == "test_generated" for name, _ in _name_stores(tree.body[2], plain))
