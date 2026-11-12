"""Small AST helpers for conservative attached-test scope handling."""

from __future__ import annotations

import ast


def attribute_root(node: ast.AST) -> str | None:
    while isinstance(node, ast.Attribute):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def match_captures(node: ast.Match) -> tuple[str, ...]:
    return tuple(name for case in node.cases for name in pattern_names(case.pattern))


def pattern_names(pattern: ast.pattern) -> tuple[str, ...]:
    names = []
    for item in ast.walk(pattern):
        if isinstance(item, (ast.MatchAs, ast.MatchStar)) and item.name:
            names.append(item.name)
        elif isinstance(item, ast.MatchMapping) and item.rest:
            names.append(item.rest)
    return tuple(names)
