"""Static helpers for decorators and fixture-name forwarding."""

from __future__ import annotations

import ast
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


def _bindings(tree: ast.Module, name: str) -> int:
    """How many places in the file bind ``name`` (any scope; counted, not resolved)."""
    count = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == name and not isinstance(node.ctx, ast.Load):
            count += 1
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            count += node.name == name
        elif isinstance(node, ast.arg):
            count += node.arg == name
        elif isinstance(node, ast.alias):
            count += (node.asname or node.name.split(".")[0]) == name
        elif isinstance(node, ast.ExceptHandler | ast.MatchAs | ast.MatchStar):
            count += getattr(node, "name", None) == name
        elif isinstance(node, ast.Global | ast.Nonlocal):
            count += name in node.names
    return count


def _root_external(root: str, tree: ast.Module, index: dict[str, set[str]] | None) -> bool:
    """``root`` is bound exactly once, by a plain top-level absolute import of a module that
    is stdlib or absent from the source tree (and no tree module shares its top name)."""
    if _bindings(tree, root) != 1:
        return False
    module = None
    for stmt in tree.body:
        if isinstance(stmt, ast.Import):
            for alias in stmt.names:
                if (alias.asname or alias.name.split(".")[0]) == root:
                    module = alias.name
        elif isinstance(stmt, ast.ImportFrom):
            for alias in stmt.names:
                if (alias.asname or alias.name) == root:
                    module = None if stmt.level or not stmt.module else stmt.module
    if module is None or index is None:
        return False
    top = module.split(".")[0]
    if any(candidate.split(".")[0] == top for candidate in index):
        return False
    return True


def external_decorator(
    decorator: ast.expr, tree: ast.Module, index: dict[str, set[str]] | None
) -> bool:
    """Whether a decorator is known to resolve outside the base source tree (fail closed).

    Only a name bound once, by a top-level absolute import of a module the source tree does
    not define, qualifies; a call decorator may take only literals or such external names, so
    ``partial(in_tree_factory)`` still counts. Anything else stays a possible factory.
    """
    target = decorator.func if isinstance(decorator, ast.Call) else decorator
    if isinstance(target, ast.Name):
        root = target.id
    elif isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name):
        root = target.value.id
    else:
        return False
    if not _root_external(root, tree, index):
        return False
    if isinstance(decorator, ast.Call):
        for arg in (*decorator.args, *(k.value for k in decorator.keywords)):
            if isinstance(arg, ast.Constant):
                continue
            inner = arg.value if isinstance(arg, ast.Attribute) else arg
            if not (isinstance(inner, ast.Name) and _root_external(inner.id, tree, index)):
                return False
    return True


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
