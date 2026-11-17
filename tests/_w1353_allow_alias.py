"""Alias and scope helpers for the witness grammar (pinned). Standard library only."""

from __future__ import annotations

import ast
from typing import Any


def raw_alias(value: ast.AST, raw: set[str], modules: dict[str, str]) -> bool:
    names = raw | modules.keys()
    return (isinstance(value, ast.Name) and value.id in names) or (
        isinstance(value, (ast.List, ast.Tuple, ast.Set, ast.Dict))
        and any(isinstance(item, ast.Name) and item.id in names for item in ast.walk(value))
    )


def scope_nodes(scope: ast.AST) -> Any:
    pending = list(ast.iter_child_nodes(scope))
    while pending:
        node = pending.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            yield node
            continue
        yield node
        pending.extend(ast.iter_child_nodes(node))


def function_for(node, tree):
    found = [
        fn
        for fn in ast.walk(tree)
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and node in ast.walk(fn)
    ]
    return min(found, key=lambda fn: sum(1 for _ in ast.walk(fn))) if found else None
