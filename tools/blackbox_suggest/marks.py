"""Arguments of marks and fixtures that pytest calls or evaluates after it imports a module.

pytest calls a parametrize or fixture ``ids`` callable and a callable fixture ``scope`` while it
collects, evaluates skipif and xfail conditions at setup (a string with ``eval``, in the module's
globals) and imports the module of a dotted filterwarnings category. Any of these can run code of
the test file after import and change what other tests see, so the reader keeps a module only
when ``ids`` is a literal (or ``str``/``repr`` never rebound), ``scope`` a literal, every
condition ``True`` or ``False``, every filter a literal of a standard-library category, and no
argument names a ``def`` or ``class`` of the file, directly or through an alias.
"""

from __future__ import annotations

import ast
import sys

from .resolve import dotted

SAFE_IMPORTS = {"pytest", "unittest", "_pytest"} | set(sys.stdlib_module_names)
ID_BUILTINS = {"repr", "str", "type"}
CONDITION_MARKS = {"skipif", "xfail"}


def callables(tree: ast.Module) -> set[str]:
    """Names bound to a def or class anywhere in the file, and names assigned from those."""
    defs = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
    found = {n.name for n in ast.walk(tree) if isinstance(n, defs)}
    assigns = [
        n for n in ast.walk(tree) if isinstance(n, ast.Assign | ast.AnnAssign | ast.NamedExpr)
    ]
    size = -1
    while size != len(found):
        size = len(found)
        for n in assigns:
            if n.value is not None and _names(n.value) & found:
                targets = n.targets if isinstance(n, ast.Assign) else [n.target]
                found |= {name for t in targets for name in _names(t)}
    return found


def later(node: ast.AST, defined: set[str], rebound: set[str]) -> list[str]:
    """Why the marks and fixture calls in node run code later; defined is ``callables`` of the
    file, rebound the names an import or anything else binds (``*`` after a star import)."""
    reasons: list[str] = []
    for call in (n for n in ast.walk(node) if isinstance(n, ast.Call)):
        name = (dotted(call.func) or [""])[-1]
        given = {k.arg: k.value for k in call.keywords if k.arg}
        if name == "parametrize":  # argnames, argvalues, indirect, ids, scope
            given |= dict(zip(("ids", "scope"), call.args[3:5], strict=False))
        args = [*call.args, *(k.value for k in call.keywords)]
        if any(_names(a) & defined for a in args):
            reasons.append("a mark or fixture argument names a def or class of this file")
        if "ids" in given and not _literal_ids(given["ids"], defined | rebound):
            reasons.append("ids are not literal (pytest calls an ids callable for each case)")
        if "scope" in given and not isinstance(given["scope"], ast.Constant):
            reasons.append("a scope is not a literal (pytest calls a callable scope)")
        conditions = [*call.args, given.get("condition")] if name in CONDITION_MARKS else []
        if not all(_bool(c) for c in conditions if c is not None):
            reasons.append("a skipif or xfail condition is not True or False (pytest evaluates it)")
        if name == "filterwarnings" and not all(_filter(a) for a in args):
            reasons.append("a filterwarnings mark is not a literal filter of a stdlib category")
    return reasons


def _names(node: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def _literal_ids(node: ast.expr, bound: set[str]) -> bool:
    if isinstance(node, ast.List | ast.Tuple):
        return all(isinstance(e, ast.Constant) for e in node.elts)
    if isinstance(node, ast.Name):
        return node.id in ID_BUILTINS and not {node.id, "*"} & bound
    return isinstance(node, ast.Constant)


def _bool(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, bool)


def _filter(node: ast.expr) -> bool:
    """``action:message:category:...`` whose category, if dotted, is from the standard library."""
    if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
        return False
    category = (node.value.split(":") + ["", ""])[2].strip()
    return "." not in category or category.split(".")[0] in SAFE_IMPORTS
