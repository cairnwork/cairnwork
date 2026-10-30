"""Import aliases of pytest's own names, resolved before anything is classified.

The readers look at spellings (``fixture``, ``mark.parametrize``, ``pytest.importorskip``), so
``from pytest import fixture as mark`` or ``import pytest as pt`` would otherwise be read as
something else. ``resolve_aliases`` rewrites such a module in place so that every use carries
the real name; the rest of the tool then treats the aliased and the plain spelling alike.
An alias is resolved only if nothing else in the module binds or declares its name (any scope,
any binding form) and the module binds no names dynamically; otherwise it is left alone, and the
scope reader refuses the unresolved alias.
"""

from __future__ import annotations

import ast
from collections import Counter

PYTEST_MODULES = {"pytest", "_pytest.mark"}


DYNAMIC = {  # names through which a module can bind names this reader does not see
    "globals", "vars", "locals", "exec", "eval", "setattr", "delattr",
    "__dict__", "__setattr__", "__delattr__",
}  # fmt: skip


def _names(node: ast.AST) -> list[str]:
    """The names node binds or declares, in whatever scope (an attribute store counts as well:
    it may be the module's)."""
    if isinstance(node, ast.Name | ast.Attribute) and isinstance(node.ctx, ast.Store | ast.Del):
        return [node.id if isinstance(node, ast.Name) else node.attr]
    if isinstance(node, ast.Import | ast.ImportFrom):
        return [(a.asname or a.name).split(".")[0] for a in node.names]
    if isinstance(node, ast.Global | ast.Nonlocal):
        return list(node.names)
    if isinstance(node, ast.arg):
        return [node.arg]
    if isinstance(node, ast.MatchMapping):
        return [node.rest] if node.rest else []
    found = [p.name for p in getattr(node, "type_params", ())]  # PEP 695, Python 3.12+
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
        found.append(node.name)
    elif isinstance(node, ast.ExceptHandler | ast.MatchAs | ast.MatchStar) and node.name:
        found.append(node.name)
    return found


def _bindings(tree: ast.Module) -> Counter[str] | None:
    """How many times each name is bound or declared anywhere in the module, by any statement
    (None: the module can bind names dynamically or by a star import, so no count is known)."""
    count: Counter[str] = Counter()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in DYNAMIC:
            return None
        if isinstance(node, ast.Attribute) and node.attr in DYNAMIC:
            return None
        count.update(_names(node))
    return None if "*" in count else count


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
    bound = _bindings(tree)
    table = _table(tree, bound) if bound is not None else {}
    return _Rename(table).visit(tree) if table else tree
