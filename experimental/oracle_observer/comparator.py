"""Strict parse of the probe's output document and the comparison with the expected values.

The document is untrusted data written inside the sandbox. Shape: ``{"v": {key: value},
"r": {key: "module.QualName"}}`` (``observe`` and ``observe_raises``). Every rule is checked at
parse time: size cap before parsing, one document and no trailing bytes, no duplicate key at any
depth, no NaN, Infinity or overflowing float, no integer over 64 digits, nesting depth at most 32.
Equality is type-strict: bool, int and float never equal each other. A float compares within the
key's absolute tolerance (finite, >= 0; floats only)."""

from __future__ import annotations

import json
import math
from typing import Any

MAX_BYTES = 1 << 20
MAX_DIGITS = 64
MAX_DEPTH = 32


class Invalid(ValueError):
    """The bytes are not an acceptable document (the reason is for the private log only)."""


def _pairs(items):
    keys = [k for k, _ in items]
    if len(set(keys)) != len(keys):
        raise Invalid("duplicate key")
    return dict(items)


def _constant(_name):
    raise Invalid("NaN or Infinity")


def _int(text):
    if len(text.lstrip("-")) > MAX_DIGITS:
        raise Invalid("integer too long")
    return int(text)


def _float(text):
    value = float(text)
    if not math.isfinite(value):
        raise Invalid("float out of range")
    return value


def _depth(value) -> int:
    deepest, stack = 0, [(value, 1)]
    while stack:
        item, level = stack.pop()
        if isinstance(item, (dict, list)):
            deepest = max(deepest, level)
            if deepest > MAX_DEPTH:
                return deepest
            stack.extend(
                (c, level + 1) for c in (item.values() if isinstance(item, dict) else item)
            )
    return deepest


def parse_strict(raw: bytes):
    """One JSON value from ``raw`` under the rules above, or ``Invalid``."""
    if len(raw) > MAX_BYTES:
        raise Invalid("over the size cap")
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_pairs,
            parse_constant=_constant,
            parse_int=_int,
            parse_float=_float,
        )
    except Invalid:
        raise
    except (ValueError, RecursionError) as exc:  # includes trailing bytes and bad UTF-8
        raise Invalid(type(exc).__name__) from exc
    if _depth(value) > MAX_DEPTH:
        raise Invalid("nested too deep")
    return value


def parse_document(raw: bytes) -> tuple[dict, dict]:
    """``(values, raises)`` of a probe document, or ``Invalid``."""
    doc = parse_strict(raw)
    if not isinstance(doc, dict) or set(doc) != {"v", "r"}:
        raise Invalid("wrong document shape")
    values, raises = doc["v"], doc["r"]
    if not isinstance(values, dict) or not isinstance(raises, dict):
        raise Invalid("wrong document shape")
    if not all(isinstance(x, str) for x in raises.values()) or set(values) & set(raises):
        raise Invalid("wrong document shape")
    return values, raises


def same(got, want, tol=None) -> bool:
    """Type-strict equality; floats within ``tol`` (absolute) when it is given."""
    if type(got) is not type(want):
        return False
    if isinstance(want, dict):
        return got.keys() == want.keys() and all(same(got[k], want[k], tol) for k in want)
    if isinstance(want, list):
        return len(got) == len(want) and all(
            same(g, w, tol) for g, w in zip(got, want, strict=True)
        )
    if isinstance(want, float) and tol is not None:
        return abs(got - want) <= tol
    return got == want


def matches(doc: tuple[dict, dict], expected: Any) -> bool:
    # expected is expected.Expected (not imported: that module imports this one)
    """True when the key sets are exact and every value agrees."""
    values, raises = doc
    if values.keys() != expected.values.keys() or raises.keys() != expected.raises.keys():
        return False
    ok = all(same(values[k], v, expected.tolerance.get(k)) for k, v in expected.values.items())
    return ok and all(raises[k] == v for k, v in expected.raises.items())
