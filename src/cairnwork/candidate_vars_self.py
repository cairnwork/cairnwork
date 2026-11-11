"""Narrow static proof for the honest ``vars(self)`` method idiom (#1155)."""

from __future__ import annotations

import ast


def safe_vars_self_statement(tree: ast.Module, statement: ast.AST) -> bool:
    """Return true only if all vars uses in this statement are safe direct method calls."""
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    calls = [
        node for node in ast.walk(statement) if isinstance(node, ast.Name) and node.id == "vars"
    ]
    if not calls or _vars_rebound(tree):
        return False
    for name in calls:
        parent = parents.get(name)
        if not isinstance(parent, ast.Call) or parent.func is not name:
            return False
        if len(parent.args) != 1 or parent.keywords or isinstance(parent.args[0], ast.Starred):
            return False
        receiver = parent.args[0]
        if not isinstance(receiver, ast.Name):
            return False
        method = _method_for(name, receiver.id, parents)
        if method is None or _rebound_in_method(method, receiver.id):
            return False
        if _unbound_method_reference(tree, method, parents):
            return False
    return True


def _method_for(
    node: ast.AST, receiver_name: str, parents: dict[ast.AST, ast.AST]
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    current = node
    while current in parents:
        current = parents[current]
        if isinstance(current, ast.Lambda):
            return None
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            method = current
            owner = parents.get(method)
            if not isinstance(owner, ast.ClassDef):
                return None
            args = [*method.args.posonlyargs, *method.args.args]
            if not args:
                return None
            receiver = args[0].arg
            if any(_decorator_name(item) == "staticmethod" for item in method.decorator_list):
                return None
            if receiver_name != receiver:
                return None
            return method
    return None


def _decorator_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        return node.func.id
    return None


def _vars_rebound(tree: ast.Module) -> bool:
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Name)
            and node.id == "vars"
            and isinstance(node.ctx, (ast.Store, ast.Del))
        ):
            return True
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and node.name == "vars"
        ):
            return True
        if isinstance(node, ast.arg) and node.arg == "vars":
            return True
        if isinstance(node, (ast.Global, ast.Nonlocal)) and "vars" in node.names:
            return True
        if isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar, ast.MatchMapping)):
            if getattr(node, "name", None) == "vars" or getattr(node, "rest", None) == "vars":
                return True
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if any((alias.asname or alias.name.split(".")[0]) == "vars" for alias in node.names):
                return True
    return False


def _rebound_in_method(method: ast.FunctionDef | ast.AsyncFunctionDef, name: str) -> bool:
    for node in ast.walk(method):
        if (
            isinstance(node, ast.Name)
            and node.id == name
            and isinstance(node.ctx, (ast.Store, ast.Del))
        ):
            return True
        if isinstance(node, ast.arg) and node.arg == name and node is not method.args.args[0]:
            return True
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and node is not method
        ):
            if node.name == name:
                return True
        if isinstance(node, (ast.Global, ast.Nonlocal)) and name in node.names:
            return True
        if isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar, ast.MatchMapping)):
            if getattr(node, "name", None) == name or getattr(node, "rest", None) == name:
                return True
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if any((alias.asname or alias.name.split(".")[0]) == name for alias in node.names):
                return True
    return False


def _unbound_method_reference(
    tree: ast.Module,
    method: ast.FunctionDef | ast.AsyncFunctionDef,
    parents: dict[ast.AST, ast.AST],
) -> bool:
    class_names = {node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.MatchClass)
            and isinstance(node.cls, ast.Name)
            and node.cls.id in class_names
        ):
            return True
        if node is method or _inside(node, method, parents):
            continue
        if isinstance(node, ast.Name) and node.id == method.name and isinstance(node.ctx, ast.Load):
            return True
        if (
            isinstance(node, ast.Attribute)
            and node.attr == method.name
            and isinstance(node.value, ast.Name)
            and node.value.id in class_names
        ):
            return True
    return False


def _inside(node: ast.AST, ancestor: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    while node in parents:
        node = parents[node]
        if node is ancestor:
            return True
    return False
