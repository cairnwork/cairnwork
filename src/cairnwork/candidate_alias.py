"""Detect aliased import machinery mutations and run-time written shadows (#1115)."""

from __future__ import annotations

import ast
import copy
from collections import Counter
from collections.abc import Sequence

from cairnwork.candidate_alias_writes import (
    _exclusive_imported_names,
    _imported_names,
    _imported_openers,
    _is_write_statement,
    _module_aliases,
)

_BLOCK_FIELDS = ("body", "orelse", "finalbody", "handlers", "cases")
_DEF_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
_PARSE_ERRORS = (SyntaxError, ValueError, UnicodeError, RecursionError, MemoryError)

TABLES = frozenset({"modules", "meta_path", "path_hooks", "path_importer_cache"})

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
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
                value = node.value
                if value is None:
                    continue
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    sys_names.update(_sys_bound_names(target, value, sys_names))
            elif isinstance(node, (ast.For, ast.AsyncFor)):
                sys_names.update(_sys_bound_names(node.target, node.iter, sys_names))
            elif isinstance(node, (ast.With, ast.AsyncWith)):
                for item in node.items:
                    if item.optional_vars is not None:
                        sys_names.update(
                            _sys_bound_names(item.optional_vars, item.context_expr, sys_names)
                        )
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                positional = [*node.args.posonlyargs, *node.args.args]
                defaults = [None] * (len(positional) - len(node.args.defaults)) + list(
                    node.args.defaults
                )
                for arg, default in zip(positional, defaults, strict=False):
                    if isinstance(default, ast.Name) and default.id in sys_names:
                        sys_names.add(arg.arg)
                for arg, default in zip(node.args.kwonlyargs, node.args.kw_defaults, strict=False):
                    if isinstance(default, ast.Name) and default.id in sys_names:
                        sys_names.add(arg.arg)
        changed = len(sys_names) > before
    return sys_names


def _sys_bound_names(target: ast.AST, value: ast.AST, sys_names: set[str]) -> set[str]:
    if isinstance(target, ast.Name):
        if isinstance(value, ast.Name) and value.id in sys_names:
            return {target.id}
        if isinstance(value, (ast.Tuple, ast.List)) and any(
            isinstance(item, ast.Name) and item.id in sys_names for item in value.elts
        ):
            return {target.id}
        return set()
    if isinstance(target, (ast.Tuple, ast.List)) and isinstance(value, (ast.Tuple, ast.List)):
        return set().union(
            *(
                _sys_bound_names(t, v, sys_names)
                for t, v in zip(target.elts, value.elts, strict=False)
            )
        )
    return set()


def _has_table_attr(expr: ast.AST, sys_names: set[str]) -> bool:
    return any(
        isinstance(node, ast.Attribute)
        and node.attr in TABLES
        and (
            node.attr != "modules"
            or isinstance(node.value, ast.Name)
            and node.value.id in sys_names
        )
        for node in ast.walk(expr)
    )


def _collect_table_names(tree: ast.Module, sys_names: set[str]) -> set[str]:
    table_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module == "sys" and node.level == 0:
                for alias in node.names:
                    if alias.name == "*":
                        table_names.update(TABLES)
                    elif alias.name in TABLES:
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
    called = {id(p.func) for p in ast.walk(node) if isinstance(p, ast.Call)}
    for part in ast.walk(node):
        if isinstance(part, ast.Attribute) and part.attr == "modules" and id(part) not in called:
            return True  # any receiver: ``[sys][0].modules``, a parameter, a class attribute
        if (
            isinstance(part, ast.Attribute)
            and part.attr in TABLES
            and isinstance(part.value, ast.Name)
            and part.value.id in sys_names
        ):
            return True
        if isinstance(part, ast.Attribute) and part.attr in TABLES - {"modules"}:
            return True
        if (
            isinstance(part, ast.ImportFrom)
            and part.module == "sys"
            and part.level == 0
            and any(alias.name == "*" or alias.name in TABLES for alias in part.names)
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


# ``sys.path`` is a list: only a name that exists on ``list`` and can change it can edit it, so the
# judgement is closed by the type (``tests/test_candidate_receiver_forms.py`` checks every name in
# ``dir(list)``). str, Path and os.path names are not on ``list`` and are never hot.
_PATH_HOT = frozenset(
    {
        "append", "extend", "insert", "remove", "pop", "clear", "sort", "reverse",
        "__setitem__", "__delitem__", "__iadd__", "__imul__", "__init__",
    }
)  # fmt: skip


def _foreign_path(node: ast.AST | None) -> bool:
    """``<expr>.path`` whatever the receiver: no name is trusted (``os.path`` included)."""
    return isinstance(node, ast.Attribute) and node.attr == "path"


def _edits_foreign_path(part: ast.AST) -> bool:
    """``<expr>.path`` edited, aliased or handed to a call whatever ``<expr>`` is (``os.path``
    excepted): mutator calls, element or slice assignment, ``del``, a stored alias."""
    if isinstance(part, ast.Attribute):
        # any method taken from a ``<x>.path`` receiver, called or not (stored, passed, decorator)
        return _foreign_path(part.value) and part.attr in _PATH_HOT
    if isinstance(part, ast.Call):
        return any(_foreign_path(arg) for arg in (*part.args, *(k.value for k in part.keywords)))
    targets: list[ast.expr]
    if isinstance(part, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
        targets = part.targets if isinstance(part, ast.Assign) else [part.target]
        if _foreign_path(getattr(part, "value", None)):
            return True  # a stored alias of the search path
    elif isinstance(part, ast.NamedExpr):
        return _foreign_path(part.value)  # walrus alias of the search path
    elif isinstance(part, ast.arguments):  # a default value binds the search path to a parameter
        return any(_foreign_path(d) for d in (*part.defaults, *part.kw_defaults))
    elif isinstance(part, ast.MatchValue):
        return _foreign_path(part.value)  # a pattern naming ``<x>.path``
    elif isinstance(part, ast.Delete):
        targets = part.targets
    elif isinstance(part, (ast.For, ast.AsyncFor, ast.comprehension)):
        targets = [part.target]
    elif isinstance(part, (ast.With, ast.AsyncWith)):
        targets = [i.optional_vars for i in part.items if i.optional_vars is not None]
    else:
        return False
    return any(_writes_foreign_path(t) for t in targets)


def _writes_foreign_path(target: ast.expr) -> bool:
    """A binding target that stores into ``<expr>.path`` or its elements, however nested
    (tuple, list, starred). No receiver is trusted: any write to a ``.path`` attribute counts."""
    if isinstance(target, (ast.Tuple, ast.List)):
        return any(_writes_foreign_path(t) for t in target.elts)
    if isinstance(target, ast.Starred):
        return _writes_foreign_path(target.value)
    if isinstance(target, ast.Subscript):
        return _foreign_path(target.value)  # element or slice assignment, ``del``
    return _foreign_path(target)


def _is_path_edit_statement(node: ast.AST, sys_names: set[str], text: str) -> bool:
    for part in ast.walk(node):
        if _edits_foreign_path(part):
            return True
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
    added_items = [own for key, own in new_items if key in added_counts]
    openers = _imported_openers(new_tree)
    writers = _imported_names(
        new_tree,
        frozenset({"NamedTemporaryFile", "copyfileobj"}),
        frozenset({"tempfile", "shutil"}),
    )
    fileios = _exclusive_imported_names(new_tree, frozenset({"FileIO"}), frozenset({"io"}))
    flag_openers = _imported_names(new_tree, frozenset({"open"}), frozenset({"shelve", "dbm"}))
    modules = _module_aliases(new_tree)
    added_write = any(
        _is_write_statement(own, openers, writers, modules, fileios, flag_openers)
        for own in added_items
    )
    added_path = any(_is_path_edit_statement(own, new_sys, ast.unparse(own)) for own in added_items)
    new_path = any(_is_path_edit_statement(own, new_sys, ast.unparse(own)) for _, own in new_items)
    new_write = any(
        _is_write_statement(own, openers, writers, modules, fileios, flag_openers)
        for _, own in new_items
    )
    return (added_write and new_path) or (added_path and new_write)
