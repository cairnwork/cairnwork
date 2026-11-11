"""Detect aliased import machinery mutations and run-time written shadows (#1115)."""

from __future__ import annotations

import ast
import copy
import re
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
    for part in ast.walk(node):
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


# module-level open(path, mode, ...) functions; any other receiver is treated as Path-like
_PATH_FIRST_OPENERS = frozenset(
    {"os", "io", "builtins", "gzip", "bz2", "lzma", "codecs", "tarfile"}
)
_MODE_TEXT = re.compile(r"[rwaxbtU+]{1,6}")


_MEMORY_STREAMS = frozenset({"StringIO", "BytesIO"})
_REBINDERS = frozenset(
    {"setattr", "globals", "vars", "locals", "exec", "eval", "__builtins__", "__import__"}
)


def _memory_stream_scope(tree: ast.Module) -> tuple[frozenset[str], frozenset[str]]:
    """(constructors, names): the io.StringIO/BytesIO spellings this file can trust, and the names
    bound only to one of them. Both are empty when io, StringIO or BytesIO may be rebound. A name is
    trusted only while every use reads an attribute of it (``buf.write(...)``): any store through it
    (``buf.write = ...``, ``buf[k] = ...``) or bare use (passed, returned, aliased) drops it."""
    io_ok = True
    bare: set[str] = set()
    stores: Counter[str] = Counter()
    touched: set[str] = set()
    read_through: set[int] = set()
    for part in ast.walk(tree):
        if isinstance(part, (ast.Import, ast.ImportFrom)):
            trusted = isinstance(part, ast.ImportFrom) and part.module == "io" and part.level == 0
            for alias in part.names:
                bound = alias.asname or alias.name.split(".")[0]
                stores[bound] += 1
                if bound == "io" and not (isinstance(part, ast.Import) and alias.name == "io"):
                    io_ok = False
                if bound in _MEMORY_STREAMS:
                    if not (trusted and alias.asname is None):
                        return frozenset(), frozenset()
                    bare.add(bound)
        elif isinstance(part, ast.Name) and not isinstance(part.ctx, ast.Load):
            stores[part.id] += 1
        elif isinstance(part, ast.arg):
            stores[part.arg] += 1
        elif isinstance(part, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            stores[part.name] += 1
        if (
            isinstance(part, ast.Attribute)
            and part.attr in _MEMORY_STREAMS
            and not isinstance(part.ctx, ast.Load)
        ):
            return frozenset(), frozenset()
        if isinstance(part, ast.Name) and part.id in _REBINDERS:
            return frozenset(), frozenset()
        if isinstance(part, ast.Attribute) and part.attr == "__dict__":
            return frozenset(), frozenset()
        if isinstance(part, ast.Subscript) and not isinstance(part.ctx, ast.Load):
            key = part.slice
            if isinstance(key, ast.Constant) and key.value in _MEMORY_STREAMS | {"io"}:
                return frozenset(), frozenset()
        if isinstance(part, (ast.Attribute, ast.Subscript)) and not isinstance(part.ctx, ast.Load):
            root = _root_name(part)
            if root is not None:
                touched.add(root)
        if isinstance(part, ast.Attribute) and isinstance(part.value, ast.Name):
            if isinstance(part.ctx, ast.Load):
                read_through.add(id(part.value))
        if isinstance(part, ast.Call) and isinstance(part.func, ast.Name):
            read_through.add(id(part.func))
    for part in ast.walk(tree):
        # a bare use (call argument, return, container, alias) lets the object be changed elsewhere
        if isinstance(part, ast.Name) and isinstance(part.ctx, ast.Load):
            if id(part) not in read_through:
                touched.add(part.id)
    if any(stores[name] != 1 or name in touched for name in bare):
        return frozenset(), frozenset()
    if stores["io"] != 1 or "io" in touched:
        io_ok = False
    makers = frozenset({f"io.{name}" for name in _MEMORY_STREAMS} if io_ok else set()) | frozenset(
        bare
    )

    good: Counter[str] = Counter()
    for part in ast.walk(tree):
        if (
            isinstance(part, ast.Assign)
            and len(part.targets) == 1
            and isinstance(part.targets[0], ast.Name)
        ):
            if _memory_stream_call(part.value, makers):
                good[part.targets[0].id] += 1
        elif isinstance(part, ast.withitem) and isinstance(part.optional_vars, ast.Name):
            if _memory_stream_call(part.context_expr, makers):
                good[part.optional_vars.id] += 1
    return makers, frozenset(
        name for name, n in good.items() if n == stores[name] and name not in touched
    )


def _root_name(node: ast.AST) -> str | None:
    while isinstance(node, (ast.Attribute, ast.Subscript)):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def _memory_stream_call(value: ast.AST | None, makers: frozenset[str]) -> bool:
    if not isinstance(value, ast.Call):
        return False
    func = value.func
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        return f"{func.value.id}.{func.attr}" in makers
    return isinstance(func, ast.Name) and func.id in makers


def _in_memory_receiver(receiver: ast.AST, scope: tuple[frozenset[str], frozenset[str]]) -> bool:
    makers, names = scope
    if isinstance(receiver, ast.Name):
        return receiver.id in names
    return _memory_stream_call(receiver, makers)


_NO_MEMORY_STREAMS: tuple[frozenset[str], frozenset[str]] = (frozenset(), frozenset())


def _is_write_call(
    call: ast.Call, safe: tuple[frozenset[str], frozenset[str]] = _NO_MEMORY_STREAMS
) -> bool:
    if isinstance(call.func, ast.Attribute) and call.func.attr in {"write", "writelines"}:
        return not _in_memory_receiver(call.func.value, safe)
    if isinstance(call.func, ast.Attribute) and call.func.attr in WRITE_ATTRS | {"writestr"}:
        return True
    if isinstance(call.func, ast.Name) and call.func.id in {
        "NamedTemporaryFile",
        "FileIO",
        "copyfileobj",
    }:
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
        receiver = call.func.value if isinstance(call.func, ast.Attribute) else None
        module_receiver = isinstance(receiver, ast.Name) and receiver.id in _PATH_FIRST_OPENERS
        if mode_arg is None and receiver is None:
            mode_arg = call.args[1] if len(call.args) > 1 else None
        elif mode_arg is None and call.args:
            # mode text first means a Path-like receiver, even under a module's (rebound) name;
            # otherwise a module takes the mode second, and an unknown receiver fails closed
            first = call.args[0]
            text = first.value if isinstance(first, ast.Constant) else None
            mode_first = isinstance(text, str) and _MODE_TEXT.fullmatch(text)
            if mode_first or (not isinstance(text, str) and not module_receiver):
                mode_arg = first
            elif len(call.args) > 1:
                mode_arg = call.args[1]
        if mode_arg is None and (
            any(isinstance(arg, ast.Starred) for arg in call.args)
            or any(kw.arg is None for kw in call.keywords)
        ):
            return True
        if mode_arg is not None:
            if not (isinstance(mode_arg, ast.Constant) and isinstance(mode_arg.value, str)):
                return True
            if any(ch in mode_arg.value for ch in ("w", "a", "x", "+")):
                return True
    return False


def _is_write_statement(
    node: ast.AST, safe: tuple[frozenset[str], frozenset[str]] = _NO_MEMORY_STREAMS
) -> bool:
    return any(isinstance(part, ast.Call) and _is_write_call(part, safe) for part in ast.walk(node))


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
    added_items = [own for key, own in new_items if key in added_counts]
    safe = _memory_stream_scope(new_tree)
    added_write = any(_is_write_statement(own, safe) for own in added_items)
    added_path = any(_is_path_edit_statement(own, new_sys, ast.unparse(own)) for own in added_items)
    new_write = any(_is_write_statement(own, safe) for _, own in new_items)
    new_path = any(_is_path_edit_statement(own, new_sys, ast.unparse(own)) for _, own in new_items)
    return (added_write and new_path) or (added_path and new_write)
