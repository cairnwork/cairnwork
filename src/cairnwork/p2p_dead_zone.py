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

STOPS = (ast.Return, ast.Raise, ast.Break, ast.Continue)


def live_statements(stmts: list[ast.stmt], type_checking: bool = False) -> list[ast.stmt]:
    """``stmts`` with the dead blocks removed; the originals are never mutated."""
    out: list[ast.stmt] = []
    for stmt in stmts:
        out.extend(_live(stmt, type_checking))
        if isinstance(stmt, STOPS):
            break
    return out


def _truth(test: ast.expr, tc: bool) -> bool | None:
    """The import-time truth of a constant test, or None when it is not known."""
    if isinstance(test, ast.Constant):
        return bool(test.value)
    name = test.id if isinstance(test, ast.Name) else getattr(test, "attr", None)
    return False if tc and name == "TYPE_CHECKING" else None


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
