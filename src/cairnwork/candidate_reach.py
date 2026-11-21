"""A file that binds the module search path by a from-import is judged as a whole (#1577)."""

from __future__ import annotations

import ast


def binds_search_path(tree: ast.Module) -> bool:
    """``from sys import path [as x]`` or ``from sys import *`` anywhere in the file."""
    return any(
        isinstance(node, ast.ImportFrom)
        and node.module == "sys"
        and node.level == 0
        and any(alias.name in ("path", "*") for alias in node.names)
        for node in ast.walk(tree)
    )


def is_docstring(node: ast.AST) -> bool:
    """The only statement shape recognised as harmless: a bare string expression."""
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )


def references_doc(tree: ast.Module) -> bool:
    """Whether the file reads a docstring value as data."""
    return any(
        isinstance(node, ast.Name)
        and node.id == "__doc__"
        or isinstance(node, ast.Attribute)
        and node.attr == "__doc__"
        for node in ast.walk(tree)
    )
