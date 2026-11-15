"""Resolve Python module import spellings against the base source index."""

from __future__ import annotations

import ast
from posixpath import dirname


def import_head(file: str, module: str, level: int) -> str:
    if level == 0:
        return module
    up = dirname(file)
    for _ in range(level - 1):
        up = dirname(up)
    return ".".join(filter(None, [*(up.split("/") if level else ()), module]))


def module_import_map(
    path: str, node: ast.Import | ast.ImportFrom, index: dict[str, set[str]], files
) -> dict[str, set[str]]:
    """Map local module spellings to source files, following fixture_forms' index rules."""
    result: dict[str, set[str]] = {}
    if isinstance(node, ast.ImportFrom):
        head = import_head(path, node.module or "", node.level)
        spellings = [
            (alias.asname or alias.name, f"{head}.{alias.name}".strip(".")) for alias in node.names
        ]
    else:
        spellings = []
        for alias in node.names:
            parts = alias.name.split(".")
            spellings.extend(
                [(alias.asname, alias.name)]
                if alias.asname
                else [(".".join(parts[:i]), ".".join(parts[:i])) for i in range(1, len(parts) + 1)]
            )
    for local, name in spellings:
        targets = (index.get(name) or set()) & files - {path}
        if targets:
            result.setdefault(local, set()).update(targets)
    return result
