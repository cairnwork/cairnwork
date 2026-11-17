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


def _float(value: Any) -> float | None:
    if type(value) is dict and set(value) == {FLOAT}:
        return float.fromhex(value[FLOAT])
    return None


def _fits(value: float, expected: float, tolerance: tuple[float, float] | None) -> bool:
    rel_tol, abs_tol = tolerance or (0.0, 0.0)
    return math.isclose(value, expected, rel_tol=rel_tol, abs_tol=abs_tol)


def _window_edges(expected: float, tolerance: tuple[float, float] | None) -> tuple[float, float]:
    rel_tol, abs_tol = tolerance or (0.0, 0.0)
    if expected > 0:
        return expected - max(abs_tol, rel_tol * expected), max(
            expected + abs_tol, expected / (1 - rel_tol)
        )
    if expected < 0:
        return min(expected - abs_tol, expected / (1 - rel_tol)), expected + max(
            abs_tol, rel_tol * abs(expected)
        )
    return -abs_tol, abs_tol


def _floats_meet(a: float, b: float, left, right) -> bool:
    if max((left or (0.0, 0.0))[0], (right or (0.0, 0.0))[0]) >= 1:
        return True
    candidates = {a, b, a / 2 + b / 2, *_window_edges(a, left), *_window_edges(b, right)}
    return any(_fits(value, a, left) and _fits(value, b, right) for value in candidates)


def _values_meet(a: Any, b: Any, left, right) -> bool:
    """True when one reported value would satisfy both expected values."""
    fa, fb = _float(a), _float(b)
    if fa is not None and fb is not None:
        return _floats_meet(fa, fb, left, right)
    if type(a) is list and type(b) is list:
        return len(a) == len(b) and all(
            _values_meet(x, y, left, right) for x, y in zip(a, b, strict=True)
        )
    if type(a) is dict and type(b) is dict and fa is None and fb is None:
        return a.keys() == b.keys() and all(_values_meet(a[k], b[k], left, right) for k in a)
    return type(a) is type(b) and a == b


def _distinct(left, right) -> bool:
    if left.raises or right.raises:
        return _key(left) != _key(right)
    return not _values_meet(
        json.loads(left.expected), json.loads(right.expected), _tolerance(left), _tolerance(right)
    )


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
