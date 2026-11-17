"""Alias and scope helpers for the witness grammar (pinned). Standard library only."""

from __future__ import annotations

import ast
from functools import lru_cache
from typing import Any


def scope_nodes(scope: ast.AST) -> Any:
    pending = list(ast.iter_child_nodes(scope))
    while pending:
        node = pending.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            yield node
            continue
        yield node
        pending.extend(ast.iter_child_nodes(node))


@lru_cache(maxsize=65536)
def function_for(node, tree):
    found = [
        fn
        for fn in ast.walk(tree)
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and node in ast.walk(fn)
    ]
    return min(found, key=lambda fn: sum(1 for _ in ast.walk(fn))) if found else None


def blackbox_unpack(node: ast.AST, parent: ast.AST | None, bindings) -> bool:
    """`a, b = blackbox_result(raw)`: the pinned adapter's fresh pair ends the raw flow."""
    return (
        isinstance(parent, ast.Assign)
        and parent.value is node
        and isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "blackbox_result"
        and len(parent.targets) == 1
        and isinstance(parent.targets[0], ast.Tuple)
        and len(parent.targets[0].elts) > 1
        and all(
            isinstance(item, ast.Name) and len(bindings.get(item.id, [])) == 1
            for item in parent.targets[0].elts
        )
    )


_BLACKBOX_DEF = ast.dump(
    ast.parse(
        "def blackbox_result(result):\n"
        '    return result.get("status", result.get("error", "FAILED")), result.get(\n'
        '        "reason", result.get("detail", "")\n'
        "    )\n"
    ).body[0]
)
_PROBE_MODULES = {"_w1353_probe", "tests._w1353_probe"}


def blackbox_trusted(tree: ast.Module, bindings) -> bool:
    """The file's only `blackbox_result` is the pinned probe adapter or its plain import."""
    found = bindings.get("blackbox_result", [])
    if len(found) != 1 or found[0] not in tree.body:
        return False
    node = found[0]
    if isinstance(node, ast.ImportFrom):
        local = [a for a in node.names if (a.asname or a.name) == "blackbox_result"]
        return (
            node.level == 0
            and node.module in _PROBE_MODULES
            and len(local) == 1
            and local[0].name == "blackbox_result"
        )
    return isinstance(node, ast.FunctionDef) and ast.dump(node) == _BLACKBOX_DEF


def shadowed_call(parent, tree: ast.Module, pure, bindings, local_bindings) -> bool:
    """A pure-converter call whose name is rebound (or an unpinned `blackbox_result`)."""
    if not (
        isinstance(parent, ast.Call)
        and isinstance(parent.func, ast.Name)
        and parent.func.id in pure
    ):
        return False
    if parent.func.id == "blackbox_result":
        return not blackbox_trusted(tree, bindings)
    return parent.func.id in bindings or parent.func.id in local_bindings
