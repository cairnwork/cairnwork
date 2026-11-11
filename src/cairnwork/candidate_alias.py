"""Detect aliased import machinery mutations and run-time written shadows (#1115)."""

from __future__ import annotations

import ast
import copy
from collections import Counter
from collections.abc import Sequence

_BLOCK_FIELDS = ("body", "orelse", "finalbody", "handlers", "cases")
_DEF_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
_PARSE_ERRORS = (SyntaxError, ValueError, UnicodeError, RecursionError, MemoryError)

TABLES = frozenset({"modules", "meta_path", "path_hooks", "path_importer_cache"})

WRITE_ATTRS = frozenset(
    {
        "write_text",
        "write_bytes",
        "copyfile",
        "copy",
        "copy2",
        "copytree",
        "move",
        "rename",
        "replace",
        "symlink",
        "link",
        "extract",
        "extractall",
    }
)

PATH_WORDS = frozenset(
    {
        "PYTHONPATH",
        "pythonpath",
        "addsitedir",
        "__path__",
        "syspath",
        "submodule_search_locations",
    }
)

Key = tuple[str, str]


def _parse_source(data: bytes | None) -> ast.Module | None:
    if data is None:
        return None
    try:
        return ast.parse(data)
    except _PARSE_ERRORS:
        return None


def _collect_statements(
    nodes: Sequence[ast.AST],
    scope: str,
    items: list[tuple[Key, ast.AST]],
) -> None:
    for node in nodes:
        own = copy.copy(node)
        blocks: list[tuple[str, list[ast.AST]]] = []
        for field in _BLOCK_FIELDS:
            val = getattr(node, field, None)
            if isinstance(val, list):
                blocks.append((field, val))
                setattr(own, field, [])
        text = ast.unparse(own)
        items.append(((scope, text), own))
        name_or_text = node.name if isinstance(node, _DEF_NODES) else text
        inner = f"{scope}/{name_or_text}"
        for field, block in blocks:
            _collect_statements(block, f"{inner}:{field}", items)


def _collect_sys_names(tree: ast.Module) -> set[str]:
    sys_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "sys":
                    sys_names.add(alias.asname or "sys")
        elif isinstance(node, ast.ImportFrom):
            if node.module == "os" and node.level == 0:
                for alias in node.names:
                    if alias.name == "sys":
                        sys_names.add(alias.asname or "sys")

    changed = True
    while changed:
        before = len(sys_names)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                if isinstance(node.value, ast.Name) and node.value.id in sys_names:
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            sys_names.add(target.id)
            elif isinstance(node, ast.NamedExpr):
                if isinstance(node.value, ast.Name) and node.value.id in sys_names:
                    if isinstance(node.target, ast.Name):
                        sys_names.add(node.target.id)
        changed = len(sys_names) > before
    return sys_names


def _has_table_attr(expr: ast.AST, sys_names: set[str]) -> bool:
    return any(
        isinstance(node, ast.Attribute)
        and node.attr in TABLES
        and isinstance(node.value, ast.Name)
        and node.value.id in sys_names
        for node in ast.walk(expr)
    )


def _collect_table_names(tree: ast.Module, sys_names: set[str]) -> set[str]:
    table_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module == "sys" and node.level == 0:
                for alias in node.names:
                    if alias.name in TABLES:
                        table_names.add(alias.asname or alias.name)

    changed = True
    while changed:
        before = len(table_names)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                matches = _has_table_attr(node.value, sys_names) or (
                    isinstance(node.value, ast.Name) and node.value.id in table_names
                )
                if matches:
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            table_names.add(target.id)
            elif isinstance(node, ast.NamedExpr):
                matches = _has_table_attr(node.value, sys_names) or (
                    isinstance(node.value, ast.Name) and node.value.id in table_names
                )
                if matches and isinstance(node.target, ast.Name):
                    table_names.add(node.target.id)
        changed = len(table_names) > before
    return table_names


def _is_alias_hot(node: ast.AST, sys_names: set[str], table_names: set[str]) -> bool:
    for part in ast.walk(node):
        if (
            isinstance(part, ast.Attribute)
            and part.attr in TABLES
            and isinstance(part.value, ast.Name)
            and part.value.id in sys_names
        ):
            return True
        if (
            isinstance(part, ast.ImportFrom)
            and part.module == "sys"
            and part.level == 0
            and any(alias.name in TABLES for alias in part.names)
        ):
            return True
        if isinstance(part, ast.Name) and part.id in table_names:
            return True
    return False


def _scan_alias_side(
    tree: ast.Module | None,
) -> tuple[Counter[Key], set[Key]]:
    if tree is None:
        return Counter(), set()
    sys_names = _collect_sys_names(tree)
    table_names = _collect_table_names(tree, sys_names)
    items: list[tuple[Key, ast.AST]] = []
    _collect_statements(tree.body, "", items)
    counts = Counter(key for key, _ in items)
    hot = {key for key, own in items if _is_alias_hot(own, sys_names, table_names)}
    return counts, hot


def alias_machinery_changed(old: bytes | None, new: bytes) -> bool:
    new_tree = _parse_source(new)
    if new_tree is None:
        raise ValueError("candidate source could not be parsed")
    old_tree = _parse_source(old)
    new_counts, new_hot = _scan_alias_side(new_tree)
    old_counts, old_hot = _scan_alias_side(old_tree)
    added = new_counts - old_counts
    removed = old_counts - new_counts
    return any(k in new_hot for k in added) or any(k in old_hot for k in removed)


def _is_write_call(call: ast.Call) -> bool:
    if isinstance(call.func, ast.Attribute) and call.func.attr in WRITE_ATTRS:
        return True
    is_os_open = (
        isinstance(call.func, ast.Attribute)
        and call.func.attr == "open"
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == "os"
    )
    if is_os_open:
        return True
    is_open = (isinstance(call.func, ast.Name) and call.func.id == "open") or (
        isinstance(call.func, ast.Attribute) and call.func.attr == "open"
    )
    if is_open:
        mode_arg = None
        for kw in call.keywords:
            if kw.arg == "mode":
                mode_arg = kw.value
                break
        if mode_arg is None and len(call.args) >= 2:
            mode_arg = call.args[1]
        if mode_arg is not None:
            if not (isinstance(mode_arg, ast.Constant) and isinstance(mode_arg.value, str)):
                return True
            if any(ch in mode_arg.value for ch in ("w", "a", "x", "+")):
                return True
    return False


def _is_write_statement(node: ast.AST) -> bool:
    return any(isinstance(part, ast.Call) and _is_write_call(part) for part in ast.walk(node))


def _is_path_edit_statement(node: ast.AST, sys_names: set[str], text: str) -> bool:
    for part in ast.walk(node):
        if (
            isinstance(part, ast.Attribute)
            and part.attr == "path"
            and isinstance(part.value, ast.Name)
            and (part.value.id == "sys" or part.value.id in sys_names)
        ):
            return True
    return any(word in text for word in PATH_WORDS)


def runtime_shadow_added(old: bytes | None, new: bytes) -> bool:
    new_tree = _parse_source(new)
    if new_tree is None:
        raise ValueError("candidate source could not be parsed")
    old_tree = _parse_source(old)

    new_items: list[tuple[Key, ast.AST]] = []
    _collect_statements(new_tree.body, "", new_items)
    new_counts = Counter(key for key, _ in new_items)

    old_counts: Counter[Key] = Counter()
    if old_tree is not None:
        old_items: list[tuple[Key, ast.AST]] = []
        _collect_statements(old_tree.body, "", old_items)
        old_counts = Counter(key for key, _ in old_items)

    added_counts = new_counts - old_counts
    if not added_counts:
        return False

    new_sys = _collect_sys_names(new_tree)
    has_write = False
    has_path_edit = False

    for key, own in new_items:
        if key in added_counts:
            if not has_write and _is_write_statement(own):
                has_write = True
            if not has_path_edit and _is_path_edit_statement(own, new_sys, key[1]):
                has_path_edit = True
            if has_write and has_path_edit:
                return True

    return False
