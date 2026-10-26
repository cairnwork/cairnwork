"""Static literal evaluation for blackbox suggestions: AST nodes in, JSON values out, nothing run.

A value is usable only if it is a JSON value the blackbox can carry unchanged: None, bool, int,
str, lists and string-keyed dicts. Floats are allowed through but reported (``Value.floats``) so
the caller can require the float kind. Everything else (tuples, sets, bytes, complex, f-strings,
names that are not bound to literals, calls) raises ``Refused`` with a reason, because a draft that
changed the value's type would change what the test checks.
"""

from __future__ import annotations

import ast
import math
from dataclasses import dataclass
from typing import Any

MAX_DEPTH = 50


class Refused(Exception):
    """A test (or one case of it) cannot become a blackbox check; str(exc) is the reason."""


@dataclass(frozen=True)
class Value:
    value: Any
    floats: bool  # the value holds a float somewhere: needs the float kind


Env = dict[str, ast.expr]


def evaluate(node: ast.expr, env: Env, *, what: str) -> Value:
    """The JSON value of node, resolving names through env (each name maps to an AST node)."""
    floats: list[bool] = []
    value = _eval(node, env, what, floats, 0, frozenset())
    return Value(value, any(floats))


def _eval(node: ast.expr, env: Env, what: str, floats: list[bool], depth: int, seen: frozenset):
    if depth > MAX_DEPTH:
        raise Refused(f"{what} is nested too deeply")
    nxt = depth + 1
    if isinstance(node, ast.Constant):
        return _constant(node.value, what, floats)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub | ast.UAdd):
        inner = _eval(node.operand, env, what, floats, nxt, seen)
        if type(inner) not in (int, float):
            raise Refused(f"{what}: unary sign on a non-number")
        return -inner if isinstance(node.op, ast.USub) else inner
    if isinstance(node, ast.List):
        return [_eval(item, env, what, floats, nxt, seen) for item in node.elts]
    if isinstance(node, ast.Dict):
        return _dict(node, env, what, floats, nxt, seen)
    if isinstance(node, ast.Name):
        if node.id in seen or node.id not in env:
            raise Refused(f"{what} uses the name {node.id!r}, which is not bound to a literal")
        return _eval(env[node.id], env, what, floats, nxt, seen | {node.id})
    if isinstance(node, ast.Tuple):
        raise Refused(f"{what} holds a tuple; the blackbox carries JSON lists only")
    if isinstance(node, ast.Set | ast.SetComp):
        raise Refused(f"{what} holds a set; JSON has none")
    if isinstance(node, ast.JoinedStr):
        raise Refused(f"{what} is an f-string")
    raise Refused(f"{what} is not a literal ({type(node).__name__})")


def _constant(value: Any, what: str, floats: list[bool]) -> Any:
    kind = type(value)
    if value is None or kind in (bool, int, str):
        return value
    if kind is float:
        if not math.isfinite(value):
            raise Refused(f"{what} holds NaN or infinity")
        floats.append(True)
        return value
    raise Refused(f"{what} holds a {kind.__name__}; JSON has none")


def _dict(node: ast.Dict, env: Env, what: str, floats: list[bool], depth: int, seen: frozenset):
    out: dict[str, Any] = {}
    for key, item in zip(node.keys, node.values, strict=True):
        if key is None:
            raise Refused(f"{what} unpacks a dict (**)")
        name = _eval(key, env, what, floats, depth, seen)
        if type(name) is not str:
            raise Refused(f"{what} has a non-string dict key; JSON keys are strings")
        if name in out:
            raise Refused(f"{what} repeats the dict key {name!r}")
        out[name] = _eval(item, env, what, floats, depth, seen)
    return out
