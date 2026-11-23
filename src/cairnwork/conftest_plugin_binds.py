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


def imported_plugins(tree: ast.Module) -> bool:
    """An import statement, at any depth, that binds the name ``pytest_plugins``: the name taken
    from a module, or any import renamed to it."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import | ast.ImportFrom):
            for alias in node.names:
                if (alias.asname or alias.name.split(".")[0]) == _NAME:
                    return True
    return False


_BUILTINS = frozenset({"list", "tuple", "set", "frozenset", "sorted", "str"})


def unreadable(node: ast.AST, values: Sequence[ast.AST | None]) -> bool:
    """The statement ``node`` feeds ``values`` into ``pytest_plugins`` in a way the reach check
    cannot read: a ``with`` whose value it could not see through or that is a bare name, attribute
    or item, or a call anywhere in a value other than a plain builtin conversion
    (``f(['safe'])``). A name in an assignment, loop or ``match`` subject stays as it was: a
    run-time list is not followed (#1014 r2)."""
    if isinstance(node, ast.With | ast.AsyncWith) and None in values:
        return True
    real = [value for value in values if value is not None]
    if any(_call(n) for value in real for n in ast.walk(value)):
        return True
    return isinstance(node, ast.With | ast.AsyncWith) and any(_opaque(v) for v in real)


def _call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    return not (isinstance(node.func, ast.Name) and node.func.id in _BUILTINS)


def _opaque(value: ast.AST) -> bool:
    if isinstance(value, ast.Name):
        return value.id != _NAME
    return isinstance(value, ast.Attribute | ast.Subscript)
