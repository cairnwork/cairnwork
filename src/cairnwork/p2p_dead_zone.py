"""Import-time reachability for p2p_bindings (#1139): the statements of a body that can run,
with blocks that never run at import time (``if False:``, ``while False:``, code after
``return``/``raise``/``break``/``continue``, and with ``type_checking=True`` also ``if
TYPE_CHECKING:``) left out. Both sides use it; constant tests and tails cannot be rebound.

TYPE_CHECKING is matched by name or attribute only, so a file-local ``TYPE_CHECKING = True``,
any ``x.TYPE_CHECKING`` and ``(test_x := typing).TYPE_CHECKING`` all count as dead. That is
sound only for the base side (it narrows the excuse), so ``type_checking`` is True there and
must never be set for the candidate side. Known tighter case: a base binding under a
file-local ``TYPE_CHECKING = True`` no longer excuses; the verdict stays dropped."""

from __future__ import annotations

import ast
import copy
import operator

STOPS = (ast.Return, ast.Raise, ast.Break, ast.Continue)


def live_statements(stmts: list[ast.stmt], type_checking: bool = False) -> list[ast.stmt]:
    """``stmts`` with the dead blocks removed; the originals are never mutated."""
    out: list[ast.stmt] = []
    for stmt in stmts:
        out.extend(_live(stmt, type_checking))
        if _stops(stmt):
            break
    return out


def _truth(test: ast.expr, tc: bool) -> bool | None:
    """The import-time truth of a constant test, or None when it is not known."""
    try:
        return bool(_constant(test))
    except (TypeError, ValueError, ZeroDivisionError, OverflowError):
        pass
    name = test.id if isinstance(test, ast.Name) else getattr(test, "attr", None)
    return False if tc and name == "TYPE_CHECKING" else None


def _constant(expr: ast.expr) -> object:
    """Evaluate only literal values and explicitly supported operations on them."""
    if isinstance(expr, ast.Constant):
        return expr.value
    if isinstance(expr, ast.UnaryOp):
        value = _constant(expr.operand)
        operation = {
            ast.UAdd: operator.pos,
            ast.USub: operator.neg,
            ast.Not: operator.not_,
            ast.Invert: operator.invert,
        }.get(type(expr.op))
        if operation is not None:
            return operation(value)
    elif isinstance(expr, ast.BinOp):
        left, right = _constant(expr.left), _constant(expr.right)
        operation = {
            ast.Add: operator.add,
            ast.Sub: operator.sub,
            ast.Mult: operator.mul,
            ast.Div: operator.truediv,
            ast.FloorDiv: operator.floordiv,
            ast.Mod: operator.mod,
            ast.Pow: operator.pow,
            ast.LShift: operator.lshift,
            ast.RShift: operator.rshift,
            ast.BitOr: operator.or_,
            ast.BitXor: operator.xor,
            ast.BitAnd: operator.and_,
        }.get(type(expr.op))
        if operation is not None:
            return operation(left, right)
    elif isinstance(expr, ast.BoolOp):
        values = [_constant(value) for value in expr.values]
        if isinstance(expr.op, ast.And):
            return all(values)
        if isinstance(expr.op, ast.Or):
            return any(values)
    elif isinstance(expr, ast.Compare):
        left = _constant(expr.left)
        operations = {
            ast.Eq: operator.eq,
            ast.NotEq: operator.ne,
            ast.Lt: operator.lt,
            ast.LtE: operator.le,
            ast.Gt: operator.gt,
            ast.GtE: operator.ge,
            ast.Is: operator.is_,
            ast.IsNot: operator.is_not,
            ast.In: lambda a, b: a in b,
            ast.NotIn: lambda a, b: a not in b,
        }
        for op, comparator in zip(expr.ops, expr.comparators, strict=True):
            right = _constant(comparator)
            operation = operations.get(type(op))
            if operation is None:
                raise ValueError("unsupported comparison")
            if not operation(left, right):
                return False
            left = right
        return True
    raise ValueError("not a supported literal expression")


def _stops(stmt: ast.stmt) -> bool:
    """Whether this statement necessarily ends the current suite."""
    if isinstance(stmt, STOPS):
        return True
    return (
        isinstance(stmt, ast.If)
        and bool(stmt.orelse)
        and bool(stmt.body)
        and _suite_stops(stmt.body)
        and _suite_stops(stmt.orelse)
    )


def _suite_stops(stmts: list[ast.stmt]) -> bool:
    return any(_stops(stmt) for stmt in stmts)


def _live(stmt: ast.stmt, tc: bool) -> list[ast.stmt]:
    if isinstance(stmt, ast.If) and (known := _truth(stmt.test, tc)) is not None:
        return live_statements(stmt.body if known else stmt.orelse, tc)
    if isinstance(stmt, ast.While) and _truth(stmt.test, tc) is False:
        return live_statements(stmt.orelse, tc)
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return [stmt]  # its body is its own scope
    stmt = copy.copy(stmt)
    for field in ("body", "orelse", "finalbody"):
        if isinstance(getattr(stmt, field, None), list):
            setattr(stmt, field, live_statements(getattr(stmt, field), tc))
    for field in ("handlers", "cases"):
        parts = [copy.copy(p) for p in getattr(stmt, field, ())]
        for part in parts:
            part.body = live_statements(part.body, tc)
        if parts:
            setattr(stmt, field, parts)
    return [stmt]
