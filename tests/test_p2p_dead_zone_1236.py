"""Issue #1236: fold literal-only conditions and recognize compound terminal statements."""

from __future__ import annotations

from textwrap import dedent, indent

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


def _verdict(base_tail: str, candidate_tail: str = LIVE_REDEF) -> str:
    base = PRE + TEST_A + TEST_B + base_tail
    candidate = PRE + EMPTY_A + TEST_B + candidate_tail
    got = classify({A_ID, B_ID}, {B_ID}, {"t.py"}, lambda p: candidate, lambda p: base)
    return "dropped" if A_ID in got.dropped else "removed" if A_ID in got.removed else "?"


def test_literal_only_folded_dead_block_does_not_exempt_drop() -> None:
    tail = _body("if (1 + 2 * 3) == 8:\n    def test_x(self): pass\n")
    assert _verdict(tail) == "dropped"


def test_non_constant_block_stays_live_and_exempts_drop() -> None:
    tail = _body("if value == 7:\n    def test_x(self): pass\n")
    assert _verdict(tail) == "removed"


def test_compound_that_always_finishes_hides_following_binding() -> None:
    tail = _body("if flag:\n    raise RuntimeError()\nelse:\n    return\ntest_x = replacement\n")
    assert _verdict(tail) == "dropped"
