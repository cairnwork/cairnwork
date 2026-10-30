"""Import aliases of pytest's own names, resolved before anything is classified.

The readers look at spellings (``fixture``, ``mark.parametrize``, ``pytest.importorskip``), so
``from pytest import fixture as mark`` or ``import pytest as pt`` would otherwise be read as
something else. ``resolve_aliases`` rewrites such a module in place so that every use carries
the real name; the rest of the tool then treats the aliased and the plain spelling alike.
A name that is bound more than once in the module is left alone: it does not name the import.
"""

from __future__ import annotations

import ast
from collections import Counter

PYTEST_MODULES = {"pytest", "_pytest.mark"}


def _bindings(tree: ast.Module) -> Counter[str]:
    """How many times each name is bound anywhere in the module, by any statement."""
    count: Counter[str] = Counter()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store | ast.Del):
            count[node.id] += 1
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            count[node.name] += 1
        elif isinstance(node, ast.arg):
            count[node.arg] += 1
        elif isinstance(node, ast.Import | ast.ImportFrom):
            for a in node.names:
                count[(a.asname or a.name).split(".")[0]] += 1
        elif isinstance(node, ast.ExceptHandler) and node.name:
            count[node.name] += 1
    return count


def _table(tree: ast.Module, bound: Counter[str]) -> dict[str, str]:
    """alias -> real name, for the module-level pytest imports whose alias is bound once."""
    table: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module in PYTEST_MODULES and not node.level:
            pairs = [(a.asname, a.name) for a in node.names if a.name != "*"]
        elif isinstance(node, ast.Import):
            pairs = [(a.asname, a.name) for a in node.names if a.name == "pytest"]
        else:
            continue
        for alias, real in pairs:
            if alias and alias != real and bound[alias] == 1:
                table[alias] = real
    return table


class _Rename(ast.NodeTransformer):
    def __init__(self, table: dict[str, str]) -> None:
        self.table = table

    def visit_Name(self, node: ast.Name) -> ast.Name:
        node.id = self.table.get(node.id, node.id) if isinstance(node.ctx, ast.Load) else node.id
        return node

    def visit_alias(self, node: ast.alias) -> ast.alias:
        if node.asname in self.table:
            node.asname = None
        return node


def resolve_aliases(tree: ast.Module) -> ast.Module:
    """The module with pytest's aliased names renamed to the real ones (the tree is changed
    in place and returned)."""
    table = _table(tree, _bindings(tree))
    return _Rename(table).visit(tree) if table else tree
