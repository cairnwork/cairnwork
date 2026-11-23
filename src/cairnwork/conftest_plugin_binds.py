"""#1341, #1342: ways a name other than a string literal can end up in ``pytest_plugins``.

conftest_reach.py follows the module names it can read in a ``pytest_plugins`` value. A value it
cannot read (a call, a name, a comprehension, a starred part, an imported object) may still name a
module that pytest loads, so the reach check refuses the file instead of passing it. The literal
forms (a string, a list, tuple or set of strings, strings joined by ``+``, the name itself) are
read as before."""

from __future__ import annotations

import ast
from collections.abc import Sequence

_NAME = "pytest_plugins"
_BUILTINS = frozenset({"list", "tuple", "set", "frozenset", "sorted", "str"})


def imported_plugins(tree: ast.Module) -> bool:
    """An import statement at module level (inside ``if``, ``try`` or ``with`` too, not inside a
    ``def``, ``class`` or ``lambda``, where the name is local) that binds the name
    ``pytest_plugins``: the name taken from a module, or any import renamed to it."""
    todo: list[ast.AST] = [tree]
    while todo:
        node = todo.pop()
        if isinstance(node, ast.Import | ast.ImportFrom):
            if any((a.asname or a.name.split(".")[0]) == _NAME for a in node.names):
                return True
        elif not isinstance(
            node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Lambda
        ):
            todo += ast.iter_child_nodes(node)
    return False


def unreadable(node: ast.AST, values: Sequence[ast.AST | None], tree: ast.Module) -> bool:
    """The statement ``node`` feeds ``values`` into ``pytest_plugins`` in a way the reach check
    cannot read: a ``with`` whose value it could not see through or that is a bare name, attribute
    or item, or a call anywhere in a value other than a plain builtin conversion
    (``f(['safe'])``). A name in an assignment, loop or ``match`` subject stays as it was: a
    run-time list is not followed (#1014 r2)."""
    if isinstance(node, ast.With | ast.AsyncWith) and None in values:
        return True
    real = [value for value in values if value is not None]
    bound = _bound(tree)
    if any(_call(n, bound) for value in real for n in ast.walk(value)):
        return True
    return isinstance(node, ast.With | ast.AsyncWith) and any(_opaque(v) for v in real)


def _call(node: ast.AST, bound: set[str]) -> bool:
    """A call other than a conversion by a builtin name that the module never rebinds."""
    if not isinstance(node, ast.Call):
        return False
    return not (isinstance(node.func, ast.Name) and node.func.id in _BUILTINS - bound)


def _bound(tree: ast.Module) -> set[str]:
    """Every name the module binds at module level (also inside ``if``, ``try``, ``with`` and
    ``for``): def, class, assignment, import, loop, ``with`` or walrus target. A builtin name in
    it may be anything (``list = lambda x: ...``). A parameter or a name bound inside a def,
    class or lambda is local and does not count."""
    out: set[str] = set()
    todo: list[ast.AST] = [tree]
    while todo:
        n = todo.pop()
        if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            out.add(n.name)
        elif isinstance(n, ast.Lambda):
            continue
        else:
            if isinstance(n, ast.Name) and not isinstance(n.ctx, ast.Load):
                out.add(n.id)
            elif isinstance(n, ast.alias):
                out.add(n.asname or n.name.split(".")[0])
            todo += ast.iter_child_nodes(n)
    return out


def _opaque(value: ast.AST) -> bool:
    if isinstance(value, ast.Name):
        return value.id != _NAME
    return isinstance(value, ast.Attribute | ast.Subscript)
