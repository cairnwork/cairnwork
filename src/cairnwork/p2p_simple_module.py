"""Allowlist for test modules whose import-time behavior is statically simple."""

from __future__ import annotations

import ast


def _simple_test_module(tree: ast.Module) -> bool:
    """Whether module structure and import-time expressions meet the inherited-delete allowlist."""
    return all(_module_stmt(stmt, index) for index, stmt in enumerate(tree.body))


def _module_stmt(stmt: ast.stmt, index: int) -> bool:
    if isinstance(stmt, (ast.Import, ast.ImportFrom)):
        return True
    if _docstring(stmt, index):
        return True
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return _function(stmt)
    if isinstance(stmt, ast.ClassDef):
        return _class(stmt)
    if isinstance(stmt, ast.Assign):
        if len(stmt.targets) != 1 or not isinstance(stmt.targets[0], ast.Name):
            return False
        if stmt.targets[0].id == "pytestmark":
            return _pytestmark_expr(stmt.value)
        return _literal_expr(stmt.value)
    if isinstance(stmt, ast.AnnAssign):
        return (
            isinstance(stmt.target, ast.Name)
            and stmt.value is not None
            and _simple_expr(stmt.annotation)
            and _literal_expr(stmt.value)
        )
    return False


def _class(stmt: ast.ClassDef) -> bool:
    if stmt.keywords or not all(_attribute_path(base) for base in stmt.bases):
        return False
    if not all(_decorator(item) for item in stmt.decorator_list):
        return False
    return all(_class_stmt(item, index) for index, item in enumerate(stmt.body))


def _class_stmt(stmt: ast.stmt, index: int) -> bool:
    if _docstring(stmt, index):
        return True
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return _function(stmt)
    if isinstance(stmt, ast.Assign):
        return (
            len(stmt.targets) == 1
            and isinstance(stmt.targets[0], ast.Name)
            and _literal_expr(stmt.value)
        )
    if isinstance(stmt, ast.AnnAssign):
        return (
            isinstance(stmt.target, ast.Name)
            and stmt.value is not None
            and _simple_expr(stmt.annotation)
            and _literal_expr(stmt.value)
        )
    # `pass` is the conventional empty-class body used by inherited test subclasses.
    return isinstance(stmt, ast.Pass)


def _function(stmt: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    if not all(_decorator(item) for item in stmt.decorator_list):
        return False
    args = stmt.args
    defaults = [*args.defaults, *(item for item in args.kw_defaults if item is not None)]
    annotations = [arg.annotation for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs)]
    annotations.extend(arg.annotation for arg in (args.vararg, args.kwarg) if arg is not None)
    if stmt.returns is not None:
        annotations.append(stmt.returns)
    return all(_simple_expr(item) for item in defaults + [a for a in annotations if a])


def _decorator(node: ast.expr) -> bool:
    if isinstance(node, ast.Call):
        path = _attribute_path(node.func)
        allowed = path == ("pytest", "fixture") or (
            path is not None and path[:2] == ("pytest", "mark") and len(path) > 2
        )
        return (
            allowed
            and all(_literal_expr(arg) for arg in node.args)
            and all(item.arg is not None and _literal_expr(item.value) for item in node.keywords)
        )
    path = _attribute_path(node)
    return (
        (isinstance(node, ast.Name) and node.id in {"staticmethod", "classmethod"})
        or (path == ("pytest", "fixture"))
        or (path is not None and path[:2] == ("pytest", "mark") and len(path) > 2)
    )


def _simple_expr(node: ast.expr) -> bool:
    if isinstance(node, (ast.Name, ast.Constant)):
        return True
    if isinstance(node, ast.Attribute):
        return _attribute_path(node) is not None
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return all(_simple_expr(item) for item in node.elts)
    if isinstance(node, ast.Dict):
        return all(key is None or _simple_expr(key) for key in node.keys) and all(
            _simple_expr(value) for value in node.values
        )
    return False


def _pytestmark_expr(node: ast.expr) -> bool:
    if isinstance(node, ast.Name):
        return node.id == "pytest"
    if isinstance(node, ast.Attribute):
        path = _attribute_path(node)
        return path is not None and path[0] == "pytest"
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return all(_pytestmark_expr(item) for item in node.elts)
    if isinstance(node, ast.Dict):
        return all(key is None or _pytestmark_expr(key) for key in node.keys) and all(
            _pytestmark_expr(value) for value in node.values
        )
    if isinstance(node, ast.Call):
        path = _attribute_path(node.func)
        return (
            path is not None
            and path[:2] == ("pytest", "mark")
            and len(path) > 2
            and all(_literal_expr(arg) for arg in node.args)
            and all(item.arg is not None and _literal_expr(item.value) for item in node.keywords)
        )
    return False


def _literal_expr(node: ast.expr) -> bool:
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return all(_literal_expr(item) for item in node.elts)
    if isinstance(node, ast.Dict):
        return all(key is not None and _literal_expr(key) for key in node.keys) and all(
            _literal_expr(value) for value in node.values
        )
    return False


def _attribute_path(node: ast.expr) -> tuple[str, ...] | None:
    if isinstance(node, ast.Name):
        return (node.id,)
    if isinstance(node, ast.Attribute):
        parent = _attribute_path(node.value)
        return (*parent, node.attr) if parent else None
    return None


def _docstring(stmt: ast.stmt, index: int) -> bool:
    return (
        index == 0
        and isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Constant)
        and isinstance(stmt.value.value, str)
    )
