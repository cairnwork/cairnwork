"""Closed grammar for test modules whose import-time behavior is safe to classify."""

from __future__ import annotations

import ast
import codecs
import io
import tokenize


def _utf8_source(text: str) -> bool:
    """Whether Python's source-encoding declaration resolves to UTF-8."""
    try:
        encoding, _ = tokenize.detect_encoding(io.BytesIO(text.encode("utf-8")).readline)
        return codecs.lookup(encoding).name in {"utf-8", "utf-8-sig"}
    except (SyntaxError, LookupError, UnicodeError, ValueError):
        return False


def _simple_test_module(tree: ast.Module) -> bool:
    """Accept only the closed import-time grammar used for inherited deletion."""
    test_classes: set[str] = set()
    for index, stmt in enumerate(tree.body):
        if _docstring(stmt, index) or _import(stmt) or _constant_assignment(stmt):
            continue
        if isinstance(stmt, ast.FunctionDef) and stmt.name.startswith("test_"):
            if not _test_function(stmt):
                return False
            continue
        if isinstance(stmt, ast.ClassDef) and stmt.name.startswith("Test"):
            if not _test_class(stmt, test_classes):
                return False
            test_classes.add(stmt.name)
            continue
        return False
    return True


def _import(stmt: ast.stmt) -> bool:
    if isinstance(stmt, ast.Import):
        return len(stmt.names) == 1 and stmt.names[0].name == "pytest" and not stmt.names[0].asname
    return (
        isinstance(stmt, ast.ImportFrom)
        and stmt.level == 0
        and stmt.module == "__future__"
        and bool(stmt.names)
        and all(alias.name != "*" for alias in stmt.names)
    )


def _constant_assignment(stmt: ast.stmt) -> bool:
    if not isinstance(stmt, ast.Assign) or len(stmt.targets) != 1:
        return False
    target = stmt.targets[0]
    if not isinstance(target, ast.Name):
        return False
    return (
        not (target.id.startswith("__") and target.id.endswith("__"))
        and not target.id.startswith("pytest")  # pytest reads pytestmark, pytest_plugins
        and _literal(stmt.value)
    )


def _literal(node: ast.expr) -> bool:
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return all(_literal(item) for item in node.elts)
    if isinstance(node, ast.Dict):
        return all(key is not None and _literal(key) for key in node.keys) and all(
            _literal(value) for value in node.values
        )
    return False


def _test_class(stmt: ast.ClassDef, earlier_classes: set[str]) -> bool:
    if stmt.keywords or getattr(stmt, "type_params", []) or not _decorators(stmt.decorator_list):
        return False
    if not all(isinstance(base, ast.Name) and base.id in earlier_classes for base in stmt.bases):
        return False
    return all(_class_statement(item, index) for index, item in enumerate(stmt.body))


def _class_statement(stmt: ast.stmt, index: int) -> bool:
    if _docstring(stmt, index) or isinstance(stmt, ast.Pass) or _constant_assignment(stmt):
        return True
    return (
        isinstance(stmt, ast.FunctionDef) and stmt.name.startswith("test_") and _test_function(stmt)
    )


def _test_function(stmt: ast.FunctionDef) -> bool:
    if (
        getattr(stmt, "type_params", [])
        or not _decorators(stmt.decorator_list)
        or stmt.returns is not None
    ):
        return False
    args = stmt.args
    all_args = [*args.posonlyargs, *args.args, *args.kwonlyargs]
    all_args.extend(arg for arg in (args.vararg, args.kwarg) if arg is not None)
    if any(arg.annotation is not None for arg in all_args):
        return False
    defaults = [*args.defaults, *(value for value in args.kw_defaults if value is not None)]
    return all(_literal(value) for value in defaults)


def _decorators(nodes: list[ast.expr]) -> bool:
    return all(_test_decorator(node) for node in nodes)


def _test_decorator(node: ast.expr) -> bool:
    if isinstance(node, ast.Call):
        return (
            _mark_path(node.func)
            and all(_literal(arg) for arg in node.args)
            and all(item.arg is not None and _literal(item.value) for item in node.keywords)
        )
    return _mark_path(node)


def _mark_path(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == "mark"
        and isinstance(node.value.value, ast.Name)
        and node.value.value.id == "pytest"
    )


def _docstring(stmt: ast.stmt, index: int) -> bool:
    return (
        index == 0
        and isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Constant)
        and isinstance(stmt.value.value, str)
    )
