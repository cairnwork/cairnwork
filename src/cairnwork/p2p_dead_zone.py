"""Import-time reachability for p2p_bindings (#1139): the statements of a body that can run,
with blocks that never run at import time (``if False:``, ``if TYPE_CHECKING:``, ``while
False:``, code after ``return``/``raise``) left out. Used for the base side's excuse only."""

from __future__ import annotations

import ast
import copy

STOPS = (ast.Return, ast.Raise, ast.Break, ast.Continue)


def live_statements(stmts: list[ast.stmt]) -> list[ast.stmt]:
    """``stmts`` with the dead blocks removed; the originals are never mutated."""
    out: list[ast.stmt] = []
    for stmt in stmts:
        out.extend(_live(stmt))
        if isinstance(stmt, STOPS):
            break
    return out


def _truth(test: ast.expr) -> bool | None:
    """The import-time truth of a constant test, or None when it is not known."""
    if isinstance(test, ast.Constant):
        return bool(test.value)
    if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
        return False
    if isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING":
        return False
    return None


def _live(stmt: ast.stmt) -> list[ast.stmt]:
    if isinstance(stmt, ast.If) and (known := _truth(stmt.test)) is not None:
        return live_statements(stmt.body if known else stmt.orelse)
    if isinstance(stmt, ast.While) and _truth(stmt.test) is False:
        return live_statements(stmt.orelse)
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return [stmt]  # its body is its own scope
    stmt = copy.copy(stmt)
    for field in ("body", "orelse", "finalbody"):
        if isinstance(getattr(stmt, field, None), list):
            setattr(stmt, field, live_statements(getattr(stmt, field)))
    for field in ("handlers", "cases"):
        parts = [copy.copy(p) for p in getattr(stmt, field, ())]
        for part in parts:
            part.body = live_statements(part.body)
        if parts:
            setattr(stmt, field, parts)
    return [stmt]
