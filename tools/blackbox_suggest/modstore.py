"""tools/blackbox_suggest (#370): a project module that may rebind another module's names.

The reader takes a target's last plain binding in its own module. A project module that the
drafted targets import can still change that binding, or a global the target reads, from
outside: a store or delete on an attribute of an imported name (its own module imported by
name included), a project module passed on as a value and changed where the reader does not
look, or a patcher. Any of these in such a module refuses the draft.
"""

from __future__ import annotations

import ast
from collections.abc import Callable

from .loaders import MOCK

PATCHERS = (*MOCK, "_pytest.monkeypatch", "pytest.MonkeyPatch")  # imported in any form


def rebinds_modules(
    tree: ast.Module, absolute: Callable[[ast.ImportFrom], str], is_module: Callable[[str], bool]
) -> str | None:
    """Why a module may rebind another module's attributes, or None. absolute gives the
    absolute module of a from-import; is_module tells a project module by its dotted name."""
    imported: set[str] = set()  # every name an import statement binds, in any scope
    modules: set[str] = set()  # dotted names surely bound to a project module object
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _patcher(alias.name):
                    return "it imports a patcher"
                bound = alias.asname or alias.name.split(".")[0]
                imported.add(bound)
                parts = alias.name.split(".")
                if is_module(parts[0]):
                    modules |= (
                        {bound}
                        if alias.asname
                        else {".".join(parts[: i + 1]) for i in range(len(parts))}
                    )
        elif isinstance(node, ast.ImportFrom):
            base = absolute(node)
            for alias in node.names:
                if _patcher(f"{base}.{alias.name}") or _patcher(base):
                    return "it imports a patcher"
                bound = alias.asname or alias.name
                imported.add(bound)
                if alias.name != "*" and is_module(f"{base}.{alias.name}"):
                    modules.add(bound)
    parents = {id(child): node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store | ast.Del):
            if _chain(node.value) & imported:
                return "it stores an attribute on an imported name"
        if _module_value(node, modules, parents.get(id(node))):
            return "it passes a project module on as a value"
    return None


def _patcher(name: str) -> bool:
    return any(name == p or name.startswith(f"{p}.") for p in PATCHERS)


def _chain(node: ast.expr) -> set[str]:
    """Every name and attribute along a receiver chain: K.m in K.m.SCALE = 10 names m."""
    names: set[str] = set()
    while isinstance(node, ast.Attribute | ast.Subscript | ast.Call):
        if isinstance(node, ast.Attribute):
            names.add(node.attr)
        node = node.func if isinstance(node, ast.Call) else node.value
    return names | ({node.id} if isinstance(node, ast.Name) else set())


def _dotted(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        head = _dotted(node.value)
        return None if head is None else f"{head}.{node.attr}"
    return None


def _module_value(node: ast.AST, modules: set[str], parent: ast.AST | None) -> bool:
    """A load of a project module object that is not the receiver of an attribute lookup."""
    if not isinstance(node, ast.Name | ast.Attribute) or not isinstance(node.ctx, ast.Load):
        return False
    if isinstance(parent, ast.Attribute) and parent.value is node:
        return False
    return _dotted(node) in modules
