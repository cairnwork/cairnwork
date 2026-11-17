"""Entropy precheck for owner-declared blackbox answers."""

from __future__ import annotations

import json
import math
from typing import Any

from cairnwork.blackbox import Declaration
from cairnwork.blackbox_values import FLOAT, guessable

_SCALAR_DEGENERATE = {"true", "false", "null", "0", "1", "-1", '""'}
_FLOAT_DEGENERATE = {0.0, 1.0, -1.0}


def _degenerate(item) -> bool:
    if guessable(item):
        return True
    if item.raises:
        return False
    return _value_degenerate(json.loads(item.expected))


def _value_degenerate(value: Any) -> bool:
    if type(value) is list:
        return not value or all(_value_degenerate(item) for item in value)
    if type(value) is dict:
        if set(value) == {FLOAT}:
            return float.fromhex(value[FLOAT]) in _FLOAT_DEGENERATE
        return not value or all(_value_degenerate(item) for item in value.values())
    if type(value) is float:
        return value in _FLOAT_DEGENERATE
    if type(value) in (bool, type(None), int, str):
        return json.dumps(value, separators=(",", ":"), ensure_ascii=False) in _SCALAR_DEGENERATE
    return False


def _informative(case) -> list:
    return [call for call in case.calls if not _degenerate(call)]


def _class_distinct(left, right) -> bool:
    """Two class cases differ only if no single answer sequence fits both."""
    a, b = _informative(left), _informative(right)
    return len(a) != len(b) or any(_distinct(x, y) for x, y in zip(a, b, strict=True))


def _key(item) -> str:
    if item.raises:
        raised = json.loads(item.raises)
        return f"raises:{raised['type']}:{raised.get('message', '')}"
    return item.expected


def _tolerance(item) -> tuple[float, float] | None:
    if not item.tolerance:
        return None
    raw = json.loads(item.tolerance)

    def number(value: Any) -> float:
        return float.fromhex(value[FLOAT]) if type(value) is dict else float(value)

    return number(raw.get("rel_tol", 0)), number(raw.get("abs_tol", 0))


def _expected_number(item) -> float | None:
    value = json.loads(item.expected)
    if type(value) is dict and set(value) == {FLOAT}:
        return float.fromhex(value[FLOAT])
    return None


def _match_number(item, value: float) -> bool:
    expected = _expected_number(item)
    tolerance = _tolerance(item)
    if expected is None or tolerance is None:
        return item.expected == json.dumps(value, separators=(",", ":"))
    rel_tol, abs_tol = tolerance
    return math.isclose(value, expected, rel_tol=rel_tol, abs_tol=abs_tol)


def _window_edges(expected: float, tolerance: tuple[float, float] | None) -> tuple[float, float]:
    rel_tol, abs_tol = tolerance or (0.0, 0.0)
    if rel_tol >= 1:
        return -math.inf, math.inf
    if expected > 0:
        return expected - max(abs_tol, rel_tol * expected), max(
            expected + abs_tol, expected / (1 - rel_tol)
        )
    if expected < 0:
        return min(expected - abs_tol, expected / (1 - rel_tol)), expected + max(
            abs_tol, rel_tol * abs(expected)
        )
    return -abs_tol, abs_tol


def _windows_overlap(left, right) -> bool:
    a, b = _expected_number(left), _expected_number(right)
    if a is None or b is None:
        return left.expected == right.expected
    tolerances = (_tolerance(left), _tolerance(right))
    candidates = {a, b, a / 2 + b / 2}
    for expected, tolerance in zip((a, b), tolerances, strict=True):
        candidates.update(_window_edges(expected, tolerance))
    return any(_match_number(left, value) and _match_number(right, value) for value in candidates)


def _distinct(left, right) -> bool:
    if _tolerance(left) is None and _tolerance(right) is None:
        return _key(left) != _key(right)
    return not _windows_overlap(left, right)


def _has_distinct_answers(safe_cases: list, kind: str) -> bool:
    differ = _class_distinct if kind == "class" else _distinct
    return any(
        differ(left, right)
        for index, left in enumerate(safe_cases)
        for right in safe_cases[index + 1 :]
    )


def declaration_problems(declaration: Declaration) -> tuple[str, ...]:
    """Return one fail-closed entropy problem for each check that is too guessable."""
    if not declaration.checks:
        return ("NO_CHECKS",)
    problems = []
    for check in declaration.checks:
        if check.kind == "class":
            safe = [case for case in check.cases if _informative(case)]
        else:
            safe = [case for case in check.cases if not _degenerate(case)]
        if len(safe) >= 2 and _has_distinct_answers(safe, check.kind):
            continue
        problems.append(f"LOW_ENTROPY:{check.id}" if not safe else f"TOO_FEW_CASES:{check.id}")
    return tuple(problems)
