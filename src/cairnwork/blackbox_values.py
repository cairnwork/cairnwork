"""What a blackbox case expects, and the owner's comparison (#303, blackbox.py).

An expected outcome is a value (``expected``) or a raise (``raises``: the exception's type name and,
optionally, its exact message). A value that holds floats needs a ``tolerance`` (``rel_tol``,
``abs_tol``, as math.isclose; a missing one is 0); every other part of it is compared exactly.

Floats. Core's canonical JSON has none, and the runner's text for a float is not trusted, so a
float travels as a one-key object ``{"$float": float.hex()}``: in a declaration's canonical form
(and so its commitment), in the runner's input (float arguments), and in the runner's report. The
key ``$float`` is reserved: a declaration that uses it is refused and the runner reports such a dict
as UNSERIALIZABLE, so a tag always means a float. NaN and infinities cannot be declared.

The runner reports what happened, never whether it was expected. ``judge`` decides in the owner's
process, on JSON the owner parsed itself (only dicts, lists, strings, ints, booleans, null), so no
object of the candidate's, and none of its ``__eq__``, ever takes part in a comparison. Neither the
answers, nor the expected exception or message, nor the tolerances go into the run.
"""

from __future__ import annotations

import builtins
import json
import math
import re
from typing import Any, Protocol

from cairnwork_protocol import canonical_json

BAD = "BAD_BLACKBOX"
FLOAT = "$float"
MAX_MESSAGE = 1000  # the runner cuts messages at one more, so a longer one never matches
_TYPE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*){0,15}")
_GUESSABLE = ("true", "false", "null")
_TOLERANCES = {"rel_tol", "abs_tol"}


class BlackboxRejected(ValueError):
    code = BAD


class BadOutput(ValueError):
    """The runner's report has a shape the owner does not read: the check is incomplete."""


class Expectation(Protocol):
    """A function case or one call of a class case: canonical JSON, "" when not set."""

    @property
    def expected(self) -> str: ...
    @property
    def tolerance(self) -> str: ...
    @property
    def raises(self) -> str: ...


def tagged(value: Any) -> Any:
    """A parsed declaration value with each float as its tag; the reserved key is refused."""
    kind = type(value)
    if kind is float:
        if not math.isfinite(value):
            raise BlackboxRejected("NaN and infinite floats are not supported")
        return {FLOAT: value.hex()}
    if kind is list:
        return [tagged(item) for item in value]
    if kind is dict:
        if FLOAT in value:
            raise BlackboxRejected(f"{FLOAT!r} is a reserved key")
        return {key: tagged(item) for key, item in value.items()}
    return value


def expectation(data: dict[str, Any], where: str) -> tuple[str, str, str]:
    """(expected, tolerance, raises) of a case or call: exactly one of expected and raises."""
    if ("expected" in data) == ("raises" in data):
        raise BlackboxRejected(f"{where}: exactly one of expected and raises")
    if "raises" in data:
        if "tolerance" in data:
            raise BlackboxRejected(f"{where}: a tolerance goes with a float answer, not a raise")
        return "", "", canonical_json(_raises(data["raises"], where))
    expected = tagged(data["expected"])
    if _has_float(expected) != ("tolerance" in data):
        raise BlackboxRejected(f"{where}: a float answer needs a tolerance, and only it")
    tolerance = canonical_json(_tolerance(data["tolerance"], where)) if "tolerance" in data else ""
    return canonical_json(expected), tolerance, ""


def _raises(data: Any, where: str) -> dict[str, str]:
    if not isinstance(data, dict) or not {"type"} <= set(data) <= {"type", "message"}:
        raise BlackboxRejected(f"{where}: raises must have keys ['type'] (+['message'])")
    name = data["type"]
    if not isinstance(name, str) or not _TYPE.fullmatch(name) or name.startswith("builtins."):
        raise BlackboxRejected(f"{where}: bad exception type (a dotted name; builtins are bare)")
    if "." not in name and not _builtin_exception(name):
        raise BlackboxRejected(
            f"{where}: a bare type must be a builtin exception, else module.Name"
        )
    message = data.get("message", "")
    if not isinstance(message, str) or len(message) > MAX_MESSAGE:
        raise BlackboxRejected(f"{where}: message must be a string of at most {MAX_MESSAGE}")
    return data


def _builtin_exception(name: str) -> bool:
    kind = getattr(builtins, name, None)
    return isinstance(kind, type) and issubclass(kind, BaseException)


def _tolerance(data: Any, where: str) -> dict[str, Any]:
    if not isinstance(data, dict) or not data or not set(data) <= _TOLERANCES:
        raise BlackboxRejected(f"{where}: tolerance must have rel_tol and/or abs_tol")
    for value in data.values():
        try:
            number = float(value) if type(value) in (int, float) else math.nan
        except OverflowError:
            number = math.nan
        if not 0 <= number < math.inf:
            raise BlackboxRejected(f"{where}: a tolerance is a finite number >= 0")
    return tagged(data)


def _has_float(value: Any) -> bool:
    if type(value) is dict:
        return FLOAT in value or any(_has_float(item) for item in value.values())
    return type(value) is list and any(_has_float(item) for item in value)


def plain(item: Expectation) -> dict[str, Any]:
    """The expectation as it goes into the commitment; a value without floats as before #303's
    second step, so an older declaration keeps its commitment."""
    if item.raises:
        return {"raises": json.loads(item.raises)}
    extra = {"tolerance": json.loads(item.tolerance)} if item.tolerance else {}
    return {"expected": json.loads(item.expected)} | extra


def guessable(item: Expectation) -> bool:
    """A boolean or null answer, or a raise with no message: a candidate can guess it."""
    if item.raises:
        return "message" not in json.loads(item.raises)
    return item.expected in _GUESSABLE


def judge(item: Expectation, got: Any) -> str:
    """match, mismatch or error:<type> for one reported outcome; BadOutput for any other shape."""
    if isinstance(got, dict) and set(got) == {"ok"}:
        return "match" if not item.raises and _same(item, got["ok"]) else "mismatch"
    error, message = error_of(got)
    if not item.raises:
        return f"error:{error[:80]}"
    want = json.loads(item.raises)
    same = error == want["type"] and ("message" not in want or message == want["message"])
    return "match" if same else "mismatch"


def error_of(got: Any) -> tuple[str, str | None]:
    """A reported raise: (type name, message or None); BadOutput for any other shape."""
    if not isinstance(got, dict) or set(got) not in ({"error"}, {"error", "message"}):
        raise BadOutput("outcome")
    error, message = got["error"], got.get("message")
    if not isinstance(error, str) or ("message" in got and not isinstance(message, str)):
        raise BadOutput("error")
    return error, message


def _same(item: Expectation, value: Any) -> bool:
    try:
        if not item.tolerance:
            return canonical_json(value) == item.expected
        declared = {key: _number(v) for key, v in json.loads(item.tolerance).items()}
        tolerance = {"rel_tol": 0.0, "abs_tol": 0.0} | declared  # not isclose's rel_tol=1e-9
        return _close(json.loads(item.expected), value, tolerance)
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise BadOutput(type(exc).__name__) from None


def _number(value: Any) -> float:
    return float.fromhex(value[FLOAT]) if type(value) is dict else float(value)


def _close(expected: Any, got: Any, tolerance: dict[str, float]) -> bool:
    """Exact JSON types throughout; only a declared float is compared with math.isclose."""
    if type(expected) is dict and set(expected) == {FLOAT}:
        if type(got) is not dict or set(got) != {FLOAT} or type(got[FLOAT]) is not str:
            return False
        return math.isclose(float.fromhex(got[FLOAT]), float.fromhex(expected[FLOAT]), **tolerance)
    if type(expected) is dict:
        return (
            type(got) is dict
            and set(got) == set(expected)
            and all(_close(expected[key], got[key], tolerance) for key in expected)
        )
    if type(expected) is list:
        return (
            type(got) is list
            and len(got) == len(expected)
            and all(_close(e, g, tolerance) for e, g in zip(expected, got, strict=True))
        )
    return type(got) is type(expected) and got == expected
