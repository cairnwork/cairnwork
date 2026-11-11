"""Trusted in-memory streams for the runtime-shadow write check (#1115, #1176).

``.write()`` on an ``io.StringIO``/``io.BytesIO`` stream is not a file write. Trust is narrow and
fails closed: see ``memory_stream_scope``.
"""

from __future__ import annotations

import ast
from collections import Counter

_MEMORY_STREAMS = frozenset({"StringIO", "BytesIO"})
_REBINDERS = frozenset(
    {"setattr", "globals", "vars", "locals", "exec", "eval", "__builtins__", "__import__"}
)


def memory_stream_scope(tree: ast.Module) -> tuple[frozenset[str], frozenset[str]]:
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


def in_memory_receiver(receiver: ast.AST, scope: tuple[frozenset[str], frozenset[str]]) -> bool:
    makers, names = scope
    if isinstance(receiver, ast.Name):
        return receiver.id in names
    return _memory_stream_call(receiver, makers)


NO_MEMORY_STREAMS: tuple[frozenset[str], frozenset[str]] = (frozenset(), frozenset())
