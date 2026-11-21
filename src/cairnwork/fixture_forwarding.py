"""Static helpers for decorators and fixture-name forwarding."""

from __future__ import annotations

import ast
import sys
from collections.abc import Iterator


def module_level(body: list[ast.stmt]) -> Iterator[ast.stmt]:
    """Yield statements running at module level, including conditional blocks."""
    for node in body:
        yield node
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            continue
        blocks = [getattr(node, field, []) for field in ("body", "orelse", "finalbody")]
        blocks += [
            part.body for part in (*getattr(node, "handlers", []), *getattr(node, "cases", []))
        ]
        for block in blocks:
            yield from module_level(block)


def external_decorator(
    decorator: ast.expr, tree: ast.Module, index: dict[str, set[str]] | None
) -> bool:
    """Whether an imported decorator is known to resolve outside the base source tree."""
    target = decorator.func if isinstance(decorator, ast.Call) else decorator
    if isinstance(target, ast.Name):
        root = target.id
    elif isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name):
        root = target.value.id
    else:
        return False
    modules = {
        alias.asname or alias.name.split(".")[0]: alias.name
        for stmt in ast.walk(tree)
        if isinstance(stmt, ast.Import)
        for alias in stmt.names
    }
    modules |= {
        alias.asname or alias.name: stmt.module or ""
        for stmt in ast.walk(tree)
        if isinstance(stmt, ast.ImportFrom)
        for alias in stmt.names
    }
    module = modules.get(root)
    if module is None:
        return False
    if module.split(".")[0] in sys.stdlib_module_names:
        return True
    return index is not None and not any(
        candidate == module
        or candidate.startswith(module + ".")
        or module.startswith(candidate + ".")
        for candidate in index
    )


def forwarded(tree: ast.Module) -> dict[int, set[str]]:
    """Parameters in scope for calls inside functions of a fixture-defining module."""
    if not any(getattr(node, "name", None) == "fixture" for node in module_level(tree.body)):
        return {}
    out: dict[int, set[str]] = {}
    for definition in ast.walk(tree):
        if isinstance(definition, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
            args = definition.args
            params = {
                arg.arg
                for arg in (
                    *args.posonlyargs,
                    *args.args,
                    *args.kwonlyargs,
                    args.vararg,
                    args.kwarg,
                )
                if arg
            }
            for call in ast.walk(definition):
                if isinstance(call, ast.Call):
                    out.setdefault(id(call), set()).update(params)
    return out


def forwarded_names(tree: ast.Module) -> set[str]:
    """Literal fixture names supplied to wrappers that forward a name parameter."""
    functions = {
        node.name: node
        for node in module_level(tree.body)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }
    names: set[str] = set()
    for wrapper in functions.values():
        params = [arg.arg for arg in (*wrapper.args.posonlyargs, *wrapper.args.args)]
        params += [arg.arg for arg in wrapper.args.kwonlyargs]
        forwarded_params = {
            keyword.value.id
            for call in ast.walk(wrapper)
            if isinstance(call, ast.Call)
            for keyword in call.keywords
            if keyword.arg == "name"
            and isinstance(keyword.value, ast.Name)
            and keyword.value.id in params
        }
        for call in ast.walk(tree):
            if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name):
                continue
            if call.func.id != wrapper.name:
                continue
            values = dict(zip(params, call.args, strict=False))
            values.update({keyword.arg: keyword.value for keyword in call.keywords if keyword.arg})
            for param in forwarded_params:
                value = values.get(param)
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    names.add(value.value)
    return names
