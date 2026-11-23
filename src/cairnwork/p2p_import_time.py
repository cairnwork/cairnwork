"""#1440: whether code that runs at import can reach a def the test commit added or changed.

A direct-def deletion is only ``removed`` when the deleted body is gone. A body kept as a new
def and called by module-level code, a decorator, a default value or a class body still runs
(at import), so the id stays ``dropped``. Names are matched by identifier, which over-blocks a
little and never under-blocks; a file with no changed def is never affected.
"""

from __future__ import annotations

import ast
import re

# ways to find a def of this module by a name that is computed or hidden in a string
_DYNAMIC = frozenset(
    {"globals", "locals", "vars", "getattr", "exec", "eval", "modules", "dir", "attrgetter"}
    | {"f_globals", "f_locals", "_getframe", "currentframe", "getmembers"}
)
_WORD = re.compile(r"\w+")
_DEFS = (ast.FunctionDef, ast.AsyncFunctionDef)


def import_time_reaches_changed_def(test_tree: ast.Module, base_tree: ast.Module | None) -> bool:
    """True when import-time code can run a def the commit added or changed (or cannot tell)."""
    try:
        changed = _changed_defs(test_tree, base_tree)
        if not changed:
            return False
        tainted = _taint(test_tree, changed)
        if any(name.startswith("__") and name.endswith("__") for name in tainted):
            return True  # a hook (__init_subclass__, __set_name__, ...) runs without being named
        return bool(_import_refs(test_tree) & (tainted | _DYNAMIC))
    except RecursionError:  # ast.dump of a very deep expression: not provably unreachable
        return True


def _def_table(tree: ast.AST) -> dict[str, list[str]]:
    table: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, _DEFS):
            table.setdefault(node.name, []).append(ast.dump(node))
        elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Lambda):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    table.setdefault(target.id, []).append(ast.dump(node.value))
    return {name: sorted(dumps) for name, dumps in table.items()}


def _changed_defs(test_tree: ast.Module, base_tree: ast.Module | None) -> set[str]:
    base = _def_table(base_tree) if base_tree is not None else {}
    return {name for name, dumps in _def_table(test_tree).items() if base.get(name) != dumps}


def _refs(node: ast.AST) -> set[str]:
    found = set()
    for item in ast.walk(node):
        if isinstance(item, ast.Name):
            found.add(item.id)
        elif isinstance(item, ast.Attribute):
            found.add(item.attr)
        elif isinstance(item, ast.Constant) and isinstance(item.value, str):
            found.update(_WORD.findall(item.value))
    return found


def _taint(tree: ast.Module, changed: set[str]) -> set[str]:
    """The changed defs plus every def whose body mentions a tainted name (a helper running one)."""
    bodies: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, _DEFS):
            bodies.setdefault(node.name, set()).update(_refs(node))
    tainted = set(changed)
    grew = True
    while grew:
        grew = False
        for name, refs in bodies.items():
            if name not in tainted and refs & tainted:
                tainted.add(name)
                grew = True
    return tainted


def _import_refs(tree: ast.Module) -> set[str]:
    """Names mentioned by code that runs at import: everything but function and lambda bodies."""
    found: set[str] = set()
    pending: list[ast.AST] = [tree]
    while pending:
        node = pending.pop()
        if isinstance(node, _DEFS):
            args = node.args
            pending += [*node.decorator_list, *args.defaults, *filter(None, args.kw_defaults)]
            pending += [
                a.annotation
                for a in (*args.posonlyargs, *args.args, *args.kwonlyargs)
                if a.annotation
            ]
            pending += [x for x in (args.vararg, args.kwarg, node.returns) if x is not None]
            continue
        if isinstance(node, ast.Lambda):
            pending += [*node.args.defaults, *filter(None, node.args.kw_defaults)]
            continue
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue  # a docstring or a bare literal runs nothing
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            found.add(node.id)
        elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
            found.add(node.attr)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            found.update(_WORD.findall(node.value))
        pending += list(ast.iter_child_nodes(node))
    return found
