"""Issue #1139: a binding in a block that never runs at import time must not shadow a test on
the base side (the excuse it feeds must not widen); a reachable later binding still does.

Verdicts come from the real ``p2p_collect.classify``. Base: ``TestA::test_x`` and
``TestB::test_x`` (both ``assert check()``), plus a re-definition of ``test_x`` in TestB.
Candidate: TestA emptied; TestB keeps ``test_x`` and re-defines it live."""

from __future__ import annotations

import ast
from textwrap import dedent, indent

import pytest

from cairnwork.p2p_bindings import hidden_bindings
from cairnwork.p2p_collect import classify

A_ID = "t.py::TestA::test_x"
B_ID = "t.py::TestB::test_x"
PRE = "import typing\nTYPE_CHECKING = False\n"
TEST_A = "class TestA:\n    def test_x(self): assert check()\n"
EMPTY_A = "class TestA:\n    pass\n"
TEST_B = "class TestB:\n    def test_x(self): assert check()\n"
LIVE_REDEF = "    def test_x(self): pass\n"


def _body(text: str) -> str:
    return indent(dedent(text).lstrip("\n"), "    ")


def _if(test: str) -> str:
    return _body(f"if {test}:\n    def test_x(self): pass\n")


# Blocks that never run at import time: a re-definition there rebinds nothing.
DEAD = {
    **{f"if_{n}": _if(t) for n, t in [("false", "False"), ("zero", "0"), ("none", "None")]},
    **{f"if_{n}": _if(t) for n, t in [("empty", "''"), ("float0", "0.0"), ("bytes0", "b''")]},
    "type_checking": _if("TYPE_CHECKING"),
    "typing_type_checking": _if("typing.TYPE_CHECKING"),
    "if_true_else": _body("if True:\n    pass\nelse:\n    def test_x(self): pass\n"),
    "while_false": _body("while False:\n    def test_x(self): pass\n"),
    "elif_false": _body("if c:\n    pass\nelif False:\n    def test_x(self): pass\n"),
    "in_except": _body("try:\n    pass\nexcept E:\n    if 0:\n        test_x = f\n"),
    "in_except_star": _body("try:\n    pass\nexcept* E:\n    if 0:\n        test_x = f\n"),
    "in_case": _body("match c:\n    case 1:\n        if 0:\n            test_x = f\n"),
    "after_break": _body("for _ in (1,):\n    break\n    test_x = f\n"),
    "after_continue": _body("for _ in (1,):\n    continue\n    test_x = f\n"),
    "after_raise_in_try": _body("try:\n    raise E\n    test_x = f\nexcept E:\n    pass\n"),
    "in_for_else": _body("for _ in (1,):\n    pass\nelse:\n    if 0:\n        test_x = f\n"),
    "in_finally": _body("try:\n    pass\nfinally:\n    if 0:\n        test_x = f\n"),
    # tails of the class body itself (nothing after these runs)
    "class_after_raise": _body("raise E\ntest_x = f\n"),
    "class_after_return": _body("return\ntest_x = f\n"),
    "class_after_break": _body("break\ntest_x = f\n"),
    "class_after_continue": _body("continue\ntest_x = f\n"),
}
# Shapes that still shadow (or may): the base excuse stays as wide as before.
LIVE = {
    "plain": LIVE_REDEF,
    "if_true": _body("if True:\n    def test_x(self): pass\n"),
    "if_one": _body("if 1:\n    def test_x(self): pass\n"),
    "false_else": _body("if False:\n    pass\nelse:\n    def test_x(self): pass\n"),
    "if_and": _if("False and x"),
    "if_not_false": _if("not False"),
    "if_fstring": _if("f''"),
    "if_ellipsis": _if("..."),
    "not_type_checking": _if("not TYPE_CHECKING"),
    "type_checking_else": _body("if TYPE_CHECKING:\n    pass\nelse:\n    test_x = f\n"),
    "while_true": _body("while True:\n    test_x = f\n    break\n"),
    "while_true_else": _body("while True:\n    pass\nelse:\n    test_x = f\n"),
    "while_zero_else": _body("while 0:\n    pass\nelse:\n    test_x = f\n"),
    "for_else": _body("for _ in (1,):\n    pass\nelse:\n    test_x = f\n"),
    "for_target": _body("for test_x in (1,):\n    pass\n"),
    "with_items": _body("with f() as test_x:\n    pass\n"),
    "except_as": _body("try:\n    pass\nexcept E as test_x:\n    pass\n"),
    "finally": _body("try:\n    pass\nfinally:\n    test_x = f\n"),
    "try_else": _body("try:\n    pass\nexcept E:\n    pass\nelse:\n    test_x = f\n"),
    "match_capture": _body("match c:\n    case test_x:\n        pass\n"),
    "guard_walrus": _body("match c:\n    case 1 if (test_x := 0):\n        pass\n"),
    "if_walrus": _body("if (test_x := 0):\n    pass\n"),
    "cond_break": _body("for _ in (1,):\n    if c:\n        break\n    test_x = 1\n"),
}


def _verdict(base_b: str, cand_b: str, cand_a: str = EMPTY_A, pre: str = PRE):
    base, cand = pre + TEST_A + base_b, pre + cand_a + cand_b
    got = classify({A_ID, B_ID}, {B_ID}, {"t.py"}, lambda p: cand, lambda p: base)
    return "dropped" if A_ID in got.dropped else "removed" if A_ID in got.removed else "?"


def _shapes(table):
    return pytest.mark.parametrize("tail", table.values(), ids=table.keys())


@_shapes(DEAD)
def test_dead_zone_does_not_excuse_a_dropped_test(tail):
    """Must block: main said removed (false accept); the dead re-def must not hide the drop."""
    assert _verdict(TEST_B + tail, TEST_B + LIVE_REDEF) == "dropped"


@_shapes(LIVE)
def test_still_shadowing_shape_keeps_the_base_excuse(tail):
    assert _verdict(TEST_B + tail, TEST_B + LIVE_REDEF) == "removed"


def test_known_tighter_case_file_local_type_checking_true():
    """Known tighter case (pinned, not widened): TYPE_CHECKING is matched by name only, so a
    file-local ``TYPE_CHECKING = True`` is still treated as dead; the base excuse narrows."""
    tail = _body("if TYPE_CHECKING:\n    test_x = 1\n")
    pre = "import typing\nTYPE_CHECKING = True\n"
    assert _verdict(TEST_B + tail, TEST_B + LIVE_REDEF, pre=pre) == "dropped"


TC = ("type_checking", "typing_type_checking")


@_shapes({k: v for k, v in DEAD.items() if k not in TC})
def test_benign_candidate_with_unchanged_dead_zone_is_removed(tail):
    """Benign candidate (TestA removed, TestB unchanged incl. its dead re-def): both sides drop
    constant dead zones and tails after return/raise/break/continue, so it stays removed."""
    assert _verdict(TEST_B + tail, TEST_B + tail) == "removed"


@_shapes({k: DEAD[k] for k in TC})
def test_benign_candidate_with_type_checking_block_is_dropped_known_conservative(tail):
    """Known conservative: TYPE_CHECKING is only recognised on the base side (a file-local
    ``TYPE_CHECKING = True`` makes it live), so this benign candidate stays dropped."""
    assert _verdict(TEST_B + tail, TEST_B + tail) == "dropped"


NOT_DEAD = ("if_and", "if_not_false", "while_true", "while_true_else", "if_fstring", "if_one")


@_shapes({**{k: LIVE[k] for k in NOT_DEAD}, **{k: DEAD[k] for k in TC}, "if_x": _if("x")})
def test_candidate_redef_in_a_maybe_run_block_still_blocks(tail):
    """Reverse: no base excuse; a candidate that hides TestB::test_x behind a block that may run
    (non-constant condition, or TYPE_CHECKING, never trusted on the candidate side) is dropped."""
    assert _verdict(TEST_B, TEST_B + tail) == "dropped"


@_shapes(LIVE)
def test_benign_candidate_with_unchanged_live_shape_is_removed(tail):
    assert _verdict(TEST_B + tail, TEST_B + tail) == "removed"


def _count(tail: str, *, annotations: bool) -> int:
    tree = ast.parse(PRE + TEST_B + tail)
    found = hidden_bindings(tree, "test_x", {"TestB::test_x"}, annotations=annotations)
    return sum(found.values())


@_shapes(DEAD)
def test_dead_zone_filtering_per_side(tail):
    """Base filters everything dead; the candidate side filters all but TYPE_CHECKING."""
    assert _count(tail, annotations=False) == 0
    assert (_count(tail, annotations=True) >= 1) == (tail in (DEAD[k] for k in TC))
