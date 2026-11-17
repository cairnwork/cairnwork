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
