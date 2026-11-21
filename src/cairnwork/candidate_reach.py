"""Names bound to the module search path by a from-import, read as ``sys.path`` (#1577)."""

from __future__ import annotations

import ast


def _reach_names(tree: ast.Module) -> frozenset[str]:
    """Local names bound to the search path by ``from sys import path [as x]`` (or ``*``)."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "sys" and node.level == 0:
            for alias in node.names:
                if alias.name == "path":
                    names.add(alias.asname or "path")
                elif alias.name == "*":
                    names.add("path")
    return frozenset(names)


class _AsSysPath(ast.NodeTransformer):
    """Read a bound search-path name as ``sys.path`` so the receiver rules judge it unchanged."""

    def __init__(self, names: frozenset[str]) -> None:
        self._names = names

    def visit_Name(self, node: ast.Name) -> ast.AST:
        if node.id not in self._names:
            return node
        sys_name = ast.Name(id="sys", ctx=ast.Load())
        return ast.copy_location(ast.Attribute(value=sys_name, attr="path", ctx=node.ctx), node)


def _imports_search_path(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.ImportFrom)
        and node.module == "sys"
        and node.level == 0
        and any(alias.name in ("path", "*") for alias in node.names)
    )


_NAMESPACE_NAMES = frozenset({"globals", "vars", "locals"})


def _reaches_namespace(part: ast.AST) -> bool:
    """A namespace lookup or a ``getattr`` with a non-constant name: the bound search path can be
    taken from there without its name appearing, so a file that binds it recognises none of it."""
    if isinstance(part, ast.Name):
        return part.id in _NAMESPACE_NAMES
    if isinstance(part, ast.Attribute):
        return part.attr == "__dict__"
    if isinstance(part, ast.Call) and isinstance(part.func, ast.Name) and part.func.id == "getattr":
        name = part.args[1] if len(part.args) > 1 else None
        return not (isinstance(name, ast.Constant) and isinstance(name.value, str))
    return False
