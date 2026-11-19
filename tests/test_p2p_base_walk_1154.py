"""Pin the base-side hidden-binding walk and shadow checks (#1154 item 2)."""

from __future__ import annotations

import ast

import pytest

from cairnwork.p2p_bindings import hidden_bindings
from cairnwork.p2p_collect import classify


def _split(base: str, test: str):
    return classify(
        {"t.py::test_a"},
        set(),
        {"t.py"},
        lambda path: test,
        lambda path: base,
        exposed=(),
    )


BLOCKS = {
    "class": "class Hidden:\n    def test_a(self): pass\n",
    "if": "class Hidden:\n    if True:\n        def test_a(self): pass\n",
    "with": ("class Hidden:\n    with open(__file__) as stream:\n        def test_a(self): pass\n"),
    "try": (
        "class Hidden:\n"
        "    try:\n"
        "        def test_a(self): pass\n"
        "    except Exception:\n"
        "        pass\n"
    ),
    "for": ("class Hidden:\n    for item in ():\n        def test_a(self): pass\n"),
}


@pytest.mark.parametrize("block", sorted(BLOCKS))
def test_preexisting_nested_base_binding_does_not_make_deleted_test_look_hidden(block):
    # The top-level test is collected. Hidden.Test_a is an unrelated, uncollected copy already
    # present on the base; the candidate deletes the collected definition and leaves that copy.
    base = "def test_a(): pass\n" + BLOCKS[block]
    test = BLOCKS[block]
    side = _split(base, test)
    assert side.removed == {"t.py::test_a"}
    assert side.dropped == set()


def test_base_side_compound_rebinding_is_counted_when_it_shadows_hidden_class_test():
    base = (
        "def test_a(): pass\n"
        "class Hidden:\n"
        "    def test_a(self): pass\n"
        "    if True:\n"
        "        test_a = object()\n"
    )
    test = "class Hidden:\n    def test_a(self): pass\n"  # no rebinding on the candidate side
    side = _split(base, test)
    assert side.removed == {"t.py::test_a"}
    assert side.dropped == set()


def test_base_side_walk_does_not_excuse_a_newly_nested_candidate_binding():
    base = "def test_a(): pass\n"
    test = "class Hidden:\n    if True:\n        def test_a(self): pass\n"
    side = _split(base, test)
    assert side.removed == set()
    assert side.dropped == {"t.py::test_a"}


@pytest.mark.parametrize("block", sorted(BLOCKS))
def test_hidden_binding_walk_descends_through_each_base_statement_body(block):
    tree = ast.parse(BLOCKS[block])
    found = hidden_bindings(tree, "test_a", set(), annotations=False)
    assert sum(found.values()) == 1


def test_shadowed_base_binding_is_counted_as_the_existing_excuse():
    base = "class Hidden:\n    def test_a(self): pass\n    def test_a(self): pass\n"
    tree = ast.parse(base)
    # The collected name belongs to the later definition. The earlier binding is still hidden
    # by it, and the base-side shadow walk must retain that binding as an excuse.
    found = hidden_bindings(tree, "test_a", {"Hidden::test_a"}, annotations=False)
    assert sum(found.values()) == 1


def test_base_compound_store_shadows_a_collected_nested_binding():
    tree = ast.parse(
        "class Hidden:\n    def test_a(self): pass\n    if True:\n        test_a = object()\n"
    )
    found = hidden_bindings(tree, "test_a", {"Hidden::test_a"}, annotations=False)
    assert sum(found.values()) == 1


def test_base_shadow_walk_follows_function_defaults_for_a_rebinding():
    tree = ast.parse(
        "class Hidden:\n"
        "    def test_a(self): pass\n"
        "    def helper(self, value=(test_a := object())): pass\n"
    )
    found = hidden_bindings(tree, "test_a", {"Hidden::test_a"}, annotations=False)
    assert sum(found.values()) == 1


def test_base_shadow_walk_ignores_type_checking_suite():
    tree = ast.parse(
        "class Hidden:\n"
        "    def test_a(self): pass\n"
        "    if TYPE_CHECKING:\n"
        "        test_a = object()\n"
    )
    found = hidden_bindings(tree, "test_a", {"Hidden::test_a"}, annotations=False)
    assert sum(found.values()) == 0


def test_base_shadow_walk_skips_parameter_annotations():
    tree = ast.parse(
        "class Hidden:\n"
        "    def test_a(self): pass\n"
        "    def helper(self, value: (test_a := object())): pass\n"
    )
    found = hidden_bindings(tree, "test_a", {"Hidden::test_a"}, annotations=False)
    assert sum(found.values()) == 0


def test_collected_nested_test_is_not_hidden_without_a_later_shadow():
    tree = ast.parse("class Hidden:\n    def test_a(self): pass\n")
    found = hidden_bindings(tree, "test_a", {"Hidden::test_a"}, annotations=False)
    assert sum(found.values()) == 0
