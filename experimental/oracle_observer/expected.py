"""The human-written expected values of an oracle target, and intake checks (AC3 tolerance, AC8).

File shape: ``{"values": {key: value}, "raises": {key: "module.QualName"}, "tolerance":
{key: number}}``; ``raises`` and ``tolerance`` are optional. It lives only in the runner's private
directory, never under a sandbox read root, in argv, in the environment or on an inherited fd."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from experimental.oracle_observer.comparator import Invalid, parse_strict

WEAK_INT = range(-16, 17)


class Refused(ValueError):
    """Intake refuses the target; the text is a clear reason for the person who wrote it."""


@dataclass(frozen=True)
class Expected:
    values: dict
    raises: dict = field(default_factory=dict)
    tolerance: dict = field(default_factory=dict)


def is_strong(value) -> bool:
    """A value that is hard to guess: not a bool or None, an int outside [-16, 16], a string of
    at least two characters, or a non-empty container."""
    if value is None or isinstance(value, bool):
        return False
    if isinstance(value, int):
        return value not in WEAK_INT
    if isinstance(value, (str, list, dict)):
        return len(value) >= (2 if isinstance(value, str) else 1)
    return isinstance(value, float)


def strong_keys(expected: Expected) -> tuple[str, ...]:
    """Keys of ``values`` that are strong (an ``observe_raises`` key never is)."""
    return tuple(k for k, v in expected.values.items() if is_strong(v))


def load_expected(raw: bytes) -> Expected:
    """Parse and check an expected file; ``Refused`` for anything intake does not accept."""
    try:
        data = parse_strict(raw)
    except Invalid as exc:
        raise Refused(f"expected file is not acceptable: {exc}") from exc
    if not isinstance(data, dict) or not set(data) <= {"values", "raises", "tolerance"}:
        raise Refused("expected file has the wrong shape")
    values, raises, tol = data.get("values", {}), data.get("raises", {}), data.get("tolerance", {})
    if not all(isinstance(x, dict) for x in (values, raises, tol)):
        raise Refused("expected file has the wrong shape")
    if set(values) & set(raises) or not all(isinstance(v, str) for v in raises.values()):
        raise Refused("expected file has the wrong shape")
    for key, t in tol.items():
        bad = key not in values or isinstance(t, bool) or not isinstance(t, (int, float))
        if bad or not math.isfinite(t) or t < 0:
            raise Refused("a tolerance must be a finite number >= 0 for a key in values")
    expected = Expected(values, raises, tol)
    if not strong_keys(expected):
        raise Refused("every key is weak: add at least one strong observed value")
    return expected
