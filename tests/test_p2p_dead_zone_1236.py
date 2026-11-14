"""Issue #1236: fold literal-only conditions and recognize compound terminal statements."""

from __future__ import annotations

import time
from textwrap import dedent, indent

import pytest

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


@pytest.mark.parametrize(
    "condition",
    [
        "(0 or 2) - 1",
        "(1 and 2) == 2",
        "(1 and 2) - 1",
        "(0 or 5) > 1",
        "(0 or 2) & 2",
        "(0 or 2) >> 1",
        "(0 or '') == ''",
        "(1 and 'x') == 'x'",
    ],
)
def test_boolop_keeps_the_actual_short_circuit_operand(condition: str) -> None:
    tail = _body(f"if {condition}:\n    test_x = replacement\n")
    assert _verdict("", tail) == "dropped"


@pytest.mark.parametrize(
    "condition",
    [
        "1000 is 1000",
        "-1000 is -1000",
        "'a b' is 'a b'",
        "1.5 is 1.5",
        "b'a b' is b'a b'",
    ],
)
def test_identity_comparison_is_unknown(condition: str) -> None:
    tail = _body(f"if {condition}:\n    test_x = replacement\n")
    assert _verdict("", tail) == "dropped"


@pytest.mark.parametrize("condition", ["not 0", "0 or flag", "1 or flag"])
def test_existing_short_circuit_guards_remain_conservative(condition: str) -> None:
    tail = _body(f"if {condition}:\n    test_x = replacement\n")
    assert _verdict("", tail) == "dropped"


@pytest.mark.parametrize(
    "block",
    [
        "for _ in [1]:\n    if nope:\n        break\n"
        "    else:\n        pass\n    test_x = replacement\n",
        "for _ in [1]:\n    if nope:\n        break\n"
        "    elif nope:\n        continue\n    test_x = replacement\n",
        "for _ in [1]:\n    if nope:\n        if nested:\n            break\n"
        "    test_x = replacement\n",
        "for _ in [1]:\n    if nope:\n        break\nelse:\n    test_x = replacement\n",
        "while nope:\n    break\nelse:\n    test_x = replacement\n",
        "try:\n    pass\nfinally:\n    test_x = replacement\n",
        "try:\n    risky()\nexcept Exception:\n    test_x = replacement\n",
    ],
)
def test_rebinding_after_or_inside_compound_suites_is_still_seen(block: str) -> None:
    tail = _body(block)
    assert _verdict("", tail) == "dropped"


@pytest.mark.parametrize(
    "condition",
    [
        "0 and 7**7**7**7",
        "0 and 3**10**7",
        "0 and 'a' * 10**12",
        "0 and b'a' * 10**12",
        "0 and 1 << 10**11",
        "0 and '%0999999999d' % 1",
        "0 and 10**10**10 % 7",
    ],
)
def test_unused_dos_operands_are_not_evaluated(condition: str) -> None:
    started = time.monotonic()
    tail = _body(f"if {condition}:\n    pass\n")
    assert _verdict("", tail) in {"dropped", "removed"}
    assert time.monotonic() - started < 2


@pytest.mark.parametrize(
    "condition",
    [
        "2**128",
        "1 << 128",
        "2**64 * 2**64",
        "'a' * 4097",
        "b'a' * 4097",
        "'%d' % 1",
        "(1 << 129) % 7",
    ],
)
def test_oversized_or_formatted_constants_stay_unknown(condition: str) -> None:
    tail = _body(f"if {condition}:\n    test_x = replacement\n")
    assert _verdict("", tail) == "dropped"


@pytest.mark.parametrize(
    "condition",
    [
        "(1 + 2 * 3) == 8",
        "1 - True",
        "not 1",
        "2 - 2",
        "1 > 2",
        "'a' == 'b'",
        "-0",
        "0 and 0",
    ],
)
def test_constant_false_branch_does_not_hide_a_rebinding(condition: str) -> None:
    tail = _body(f"if {condition}:\n    test_x = replacement\n")
    assert _verdict(tail) == "dropped"
    assert _verdict("", tail) == "removed"


def test_loop_else_rebinding_after_a_definite_break_is_dead() -> None:
    tail = _body(
        "for _ in [1]:\n    if flag:\n        break\n"
        "    else:\n        continue\n    test_x = replacement\n"
    )
    assert _verdict(tail) == "dropped"
    assert _verdict("", tail) == "removed"


@pytest.mark.parametrize(
    "condition",
    [
        "(2 or 0) - 1",
        "('' and 1) == ''",
        "(b'' and 1) == b''",
        "(0.5 or 0) == 0.5",
        "('x' or '') == 'x'",
    ],
)
def test_short_circuit_exit_returns_the_operand(condition: str) -> None:
    tail = _body(f"if {condition}:\n    test_x = replacement\n")
    assert _verdict("", tail) == "dropped"


_BIG = "0x1" + "0" * 33  # 2**132, a raw literal above MAX_INT_BITS


@pytest.mark.parametrize(
    "condition",
    [
        "2**128 - 2**128", "(1 << 128) - (1 << 128)", "2**64 * 2**64 - 2**64 * 2**64",
        "'a' * 4097 == ''", "b'a' * 4097 == b''", "'%d' % 1 == ''",
        f"{_BIG} % 7 == 99", f"{_BIG} // 3 == 0",
    ],
)  # fmt: skip
def test_oversized_falsy_constants_stay_unknown(condition: str) -> None:
    tail = _body(f"if {condition}:\n    test_x = replacement\n")
    assert _verdict("", tail) == "dropped"


@pytest.mark.parametrize(
    "condition",
    [
        "2**64 - 2**64", "(1 << 127) - (1 << 127)", "'a' * 4096 == ''", "b'a' * 4096 == b''",
        "(0 and 5) == 1", "not (2 or 0)", "(1 + 2 * 3) == 8",
    ],
)  # fmt: skip
def test_constants_at_the_size_limit_still_fold(condition: str) -> None:
    tail = _body(f"if {condition}:\n    test_x = replacement\n")
    assert _verdict(tail) == "dropped"
    assert _verdict("", tail) == "removed"


class _NoOperator:
    def __getattr__(self, name: str):
        def refuse(*_args: object) -> object:
            raise AssertionError(f"operator.{name} called on an oversized operand")

        return refuse


@pytest.mark.parametrize(
    "condition",
    ["2 ** 129", "1 << 129", f"{_BIG} * 3", "'a' * 5000", "b'a' * 5000", f"{_BIG} // 3"],
)
def test_oversized_operands_are_refused_before_evaluation(
    condition: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import ast

    from cairnwork import p2p_dead_zone

    monkeypatch.setattr(p2p_dead_zone, "operator", _NoOperator())
    assert p2p_dead_zone._truth(ast.parse(condition, mode="eval").body, False) is None
