"""Import-time reachability for p2p_bindings (#1139): fold literal constants and leave out
blocks that cannot run, code after a stopping statement, and ``if``/``else`` suites whose
branches all stop. With ``type_checking=True``, ``if TYPE_CHECKING:`` is also left out.

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
MAX_INT_BITS = 128
MAX_SEQUENCE_LENGTH = 4096


def live_statements(stmts: list[ast.stmt], type_checking: bool = False) -> list[ast.stmt]:
    """``stmts`` with the dead blocks removed; the originals are never mutated."""
    out: list[ast.stmt] = []
    for stmt in stmts:
        out.extend(_live(stmt, type_checking))
        if _stops(stmt):
            break
    return out


LITERAL_NODES = (
    ast.Constant,
    ast.UnaryOp,
    ast.BinOp,
    ast.BoolOp,
    ast.Compare,
    ast.unaryop,
    ast.operator,
    ast.boolop,
    ast.cmpop,
)


def _truth(test: ast.expr, tc: bool) -> bool | None:
    """The import-time truth of a constant test, or None when it is not known. Only a test made
    of literals and operators is folded: one naming anything stays unknown (live), as on main."""
    if all(isinstance(node, LITERAL_NODES) for node in ast.walk(test)):
        try:
            return bool(_constant(test))
        except (TypeError, ValueError, ZeroDivisionError, OverflowError, MemoryError):
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
            _check_binary_limits(type(expr.op), left, right)
            return _check_size(operation(left, right))
    elif isinstance(expr, ast.BoolOp):
        value = _constant(expr.values[0])
        for operand in expr.values[1:]:
            if isinstance(expr.op, ast.And) and not value:
                return value
            if isinstance(expr.op, ast.Or) and value:
                return value
            value = _constant(operand)
        return value
    elif isinstance(expr, ast.Compare):
        left = _constant(expr.left)
        operations = {
            ast.Eq: operator.eq,
            ast.NotEq: operator.ne,
            ast.Lt: operator.lt,
            ast.LtE: operator.le,
            ast.Gt: operator.gt,
            ast.GtE: operator.ge,
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


def _check_size(value: object) -> object:
    if isinstance(value, int) and value.bit_length() > MAX_INT_BITS:
        raise ValueError("integer constant is too large")
    if isinstance(value, (str, bytes)) and len(value) > MAX_SEQUENCE_LENGTH:
        raise ValueError("sequence constant is too large")
    return value


def _check_binary_limits(op: type[ast.operator], left: object, right: object) -> None:
    if op is ast.Pow and isinstance(left, int) and isinstance(right, int):
        if right < 0 or right > MAX_INT_BITS:
            raise ValueError("power exponent is too large")
        estimate = left.bit_length() * right
        if estimate > MAX_INT_BITS:
            raise ValueError("power result is too large")
    elif op in (ast.LShift, ast.RShift) and isinstance(left, int) and isinstance(right, int):
        if right < 0 or right > MAX_INT_BITS:
            raise ValueError("shift count is too large")
        if op is ast.LShift and left.bit_length() + right > MAX_INT_BITS:
            raise ValueError("shift result is too large")
        if op is ast.RShift and max(0, left.bit_length() - right) > MAX_INT_BITS:
            raise ValueError("shift result is too large")
    elif op is ast.Mult:
        if isinstance(left, int) and isinstance(right, int):
            if left.bit_length() + right.bit_length() > MAX_INT_BITS:
                raise ValueError("product is too large")
        else:
            _check_repetition(left, right)
            _check_repetition(right, left)
    elif op is ast.Mod:
        if isinstance(left, (str, bytes)):
            raise ValueError("string formatting is unsupported")
        if isinstance(left, int) and isinstance(right, int):
            _check_size(left)
            _check_size(right)


def _check_repetition(value: object, count: object) -> None:
    if isinstance(value, (str, bytes)) and isinstance(count, int):
        if len(value) * max(count, 0) > MAX_SEQUENCE_LENGTH:
            raise ValueError("repeated sequence is too large")


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
