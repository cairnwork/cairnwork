"""Issue #1139: a binding in a block that never runs at import time must not shadow a test on
the base side (the excuse it feeds must not widen); a reachable later binding still does."""

from __future__ import annotations

import ast

import pytest

from cairnwork.p2p_bindings import hidden_bindings
from cairnwork.p2p_collect import classify

COLLECTED = {"TestA::test_x"}
HEAD = "class TestA:\n    def test_x(self): assert 0\n"
DEAD = {
    "if_false": "    if False:\n        def test_x(self): pass\n",
    "if_zero": "    if 0:\n        def test_x(self): pass\n",
    "if_none": "    if None:\n        def test_x(self): pass\n",
    "if_empty_str": "    if '':\n        def test_x(self): pass\n",
    "type_checking": "    if TYPE_CHECKING:\n        def test_x(self): pass\n",
    "typing_type_checking": "    if typing.TYPE_CHECKING:\n        def test_x(self): pass\n",
    "if_true_else": "    if True:\n        pass\n    else:\n        def test_x(self): pass\n",
    "while_false": "    while False:\n        def test_x(self): pass\n",
    "nested_dead": "    if True:\n        if False:\n            def test_x(self): pass\n",
    "dead_in_try": "    try:\n        if False:\n            test_x = 1\n    except E: pass\n",
    "dead_in_with": "    with open('f'):\n        if 0:\n            test_x = 1\n",
    "dead_assign": "    if False:\n        test_x = print\n",
    "dead_import": "    if False:\n        from m import test_x\n",
}
LIVE = {
    "plain": "    def test_x(self): pass\n",
    "if_true": "    if True:\n        def test_x(self): pass\n",
    "if_one": "    if 1:\n        def test_x(self): pass\n",
    "false_else": "    if False:\n        pass\n    else:\n        def test_x(self): pass\n",
    "unknown_cond": "    if cond():\n        def test_x(self): pass\n",
    "while_false_else": "    while False:\n        pass\n    else:\n        test_x = 1\n",
    "assign": "    test_x = print\n",
    "try_body": "    try:\n        def test_x(self): pass\n    finally:\n        pass\n",
    "dead_and_live": "    if False:\n        def test_x(self): pass\n    def test_x(self): pass\n",
}


def _count(source: str, *, annotations: bool) -> int:
    tree = ast.parse(source)
    found = hidden_bindings(tree, "test_x", COLLECTED, annotations=annotations)
    return sum(found.values())


@pytest.mark.parametrize("tail", DEAD.values(), ids=DEAD.keys())
def test_dead_zone_binding_does_not_shadow_on_the_base_side(tail):
    assert _count(HEAD + tail, annotations=False) == 0


@pytest.mark.parametrize("tail", LIVE.values(), ids=LIVE.keys())
def test_reachable_later_binding_still_shadows_on_the_base_side(tail):
    assert _count(HEAD + tail, annotations=False) >= 1


@pytest.mark.parametrize("tail", LIVE.values(), ids=LIVE.keys())
def test_reachable_later_binding_still_shadows_on_the_candidate_side(tail):
    assert _count(HEAD + tail, annotations=True) >= 1


@pytest.mark.parametrize("tail", DEAD.values(), ids=DEAD.keys())
def test_candidate_side_keeps_counting_dead_zone_as_shadow(tail):
    """Only the base excuse narrows: the candidate side stays as conservative as before."""
    assert _count(HEAD + tail, annotations=True) >= 1


STOPS = {
    "return": "def f():\n    def test_x(): pass\n    return 1\n    test_x = 2\n",
    "raise": "def f():\n    def test_x(): pass\n    raise ValueError\n    test_x = 2\n",
}


@pytest.mark.parametrize("source", STOPS.values(), ids=STOPS.keys())
def test_code_after_return_or_raise_is_dead_for_the_base_excuse(source):
    tree = ast.parse("class TestA:\n    " + source.replace("\n", "\n    ").rstrip() + "\n")
    found = hidden_bindings(tree, "test_x", set(), annotations=False)
    # the function body binding itself is hidden once; the dead rebinding adds no second one
    assert sum(found.values()) == 1


@pytest.mark.parametrize("tail", DEAD.values(), ids=DEAD.keys())
def test_base_and_candidate_same_source_dead_zone_is_not_removed(tail):
    """End to end pin (passes before and after; classify's mention check also keeps it dropped):
    the same file on both sides, the test not collected on the candidate side."""
    source = "import typing\nTYPE_CHECKING = False\n" + HEAD + tail
    nodeid = "t.py::TestA::test_x"
    side = classify({nodeid}, set(), {"t.py"}, lambda p: source, lambda p: source)
    assert nodeid in side.dropped and nodeid not in side.removed
