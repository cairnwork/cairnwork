"""Program-order snapshots for same-file class aliases in deferred test collection."""

from __future__ import annotations

import ast

_DEFS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def class_aliases(body: list[ast.stmt]) -> dict[int, dict[str, str]]:
    """Snapshot unambiguous class aliases immediately before each class statement."""
    aliases: dict[str, str] = {}
    snapshots: dict[int, dict[str, str]] = {}
    classes: set[str] = set()
    for item in body:
        if isinstance(item, ast.ClassDef):
            snapshots[id(item)] = aliases.copy()
            classes.add(item.name)
            aliases[item.name] = item.name
        elif isinstance(item, ast.Assign) and len(item.targets) == 1:
            target, value = item.targets[0], item.value
            if isinstance(target, ast.Name) and isinstance(value, ast.Name):
                source = aliases.get(value.id, value.id)
                if source in classes:
                    aliases[target.id] = source
                else:
                    aliases.pop(target.id, None)
            else:
                for name in _target_names(item.targets[0]):
                    aliases.pop(name, None)
        elif isinstance(item, (ast.AnnAssign, ast.AugAssign)):
            for name in _target_names(item.target):
                aliases.pop(name, None)
        elif not isinstance(item, (_DEFS, ast.Import, ast.ImportFrom)):
            for child in ast.walk(item):
                if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store):
                    aliases.pop(child.id, None)
    return snapshots


def _target_names(target: ast.AST) -> set[str]:
    return {node.id for node in ast.walk(target) if isinstance(node, ast.Name)}
