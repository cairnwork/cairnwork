"""Conservative import-time reachability for direct-def test deletions."""

from __future__ import annotations

import ast
from collections import Counter
from collections.abc import Iterator, Sequence

_DYNAMIC = {
    "globals",
    "vars",
    "locals",
    "getattr",
    "exec",
    "eval",
    "__import__",
    "importlib",
    "import_module",
    "attrgetter",
    "methodcaller",
    "getattr_static",
    "getmembers",
}
_DYNAMIC_ATTRS = {
    "modules",
    "__dict__",
    "f_globals",
    "f_locals",
    "f_back",
    "_getframe",
    "currentframe",
    "__globals__",
    "__builtins__",
}
_NAME_ONLY = {"list", "sorted", "len", "tuple", "set", "frozenset"}
_FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef)


def _import_time_uses_changed(base: ast.Module, test: ast.Module) -> bool:
    """Whether import execution can reach a newly added or changed function definition."""
    changed = _changed_names(base, test)
    if not changed:
        return False
    rebound = _rebound(test)
    named, bound = _named(test)
    names, dynamic = _import_reads(test, bound, rebound)
    if dynamic:
        return True
    pending, reached = list(names), set()
    while pending:
        name = pending.pop()
        if name in reached:
            continue
        reached.add(name)
        if name in changed:
            return True
        for node in named.get(name, ()):
            found, dyn = _scan([node], (), rebound, dunder=False)
            if dyn:
                return True
            pending.extend(found)
            if isinstance(node, ast.ClassDef):  # protocol methods run without a named call
                pending.extend(
                    sub.name
                    for sub in ast.walk(node)
                    if isinstance(sub, _FUNCS) and sub.name.startswith("__")
                )
    return False


def _changed_names(base: ast.Module, test: ast.Module) -> set[str]:
    """Names with a function definition in ``test`` that ``base`` does not have verbatim."""
    old, new = _dumps(base), _dumps(test)
    return {name for name, dumps in new.items() if dumps - old.get(name, Counter())}


def _dumps(tree: ast.Module) -> dict[str, Counter[str]]:
    found: dict[str, Counter[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, _FUNCS):
            found.setdefault(node.name, Counter())[ast.dump(node)] += 1
    return found


def _rebound(tree: ast.Module) -> bool:
    """A builtin the name-only exemption relies on is rebound or imported under an alias."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            hit = node.id
        elif isinstance(node, (*_FUNCS, ast.ClassDef)):
            hit = node.name
        elif isinstance(node, ast.alias):
            hit = node.asname or node.name
        elif isinstance(node, ast.arg):
            hit = node.arg
        else:
            continue
        if hit in _NAME_ONLY:
            return True
    return False


def _named(tree: ast.Module) -> tuple[dict[str, list[ast.AST]], set[int]]:
    """Every definition reachable by name (functions, classes, names bound to a lambda)."""
    named: dict[str, list[ast.AST]] = {}
    bound: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (*_FUNCS, ast.ClassDef)):
            named.setdefault(node.name, []).append(node)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)) and isinstance(node.value, ast.Lambda):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    named.setdefault(target.id, []).append(node.value)
                    bound.add(id(node.value))
    return named, bound


def _walk(node: ast.AST, skip: set[int]) -> Iterator[ast.AST]:
    stack = [node]
    while stack:
        item = stack.pop()
        if id(item) in skip:
            continue
        yield item
        stack.extend(ast.iter_child_nodes(item))


def _scan(
    nodes: Sequence[ast.AST], skip: Sequence[int], rebound: bool, dunder: bool
) -> tuple[set[str], bool]:
    """Names and attribute names read by ``nodes``, and whether a namespace lookup occurs."""
    skipped, names, dynamic = set(skip), set(), False
    for item in nodes:
        everything = list(_walk(item, skipped))
        exempt = set()
        if not rebound:
            exempt = {
                id(arg)
                for call in everything
                if isinstance(call, ast.Call)
                and isinstance(call.func, ast.Name)
                and call.func.id in _NAME_ONLY
                for arg in call.args
            }
        for child in everything:
            if isinstance(child, ast.Name):
                if isinstance(child.ctx, ast.Load):
                    names.add(child.id)
                dynamic |= child.id in _DYNAMIC
            elif isinstance(child, ast.Attribute):
                names.add(child.attr)
                special = child.attr in _DYNAMIC or child.attr in _DYNAMIC_ATTRS
                special |= dunder and child.attr.startswith("__") and child.attr.endswith("__")
                dynamic |= special and not (child.attr == "__dict__" and id(child) in exempt)
            elif isinstance(child, ast.alias):
                names.add(child.name.rpartition(".")[2])
                dynamic |= child.name in _DYNAMIC
    return names, dynamic


def _import_reads(tree: ast.Module, bound: set[int], rebound: bool) -> tuple[set[str], bool]:
    """What executing the module body reads: statements, class bodies, definition headers."""
    names: set[str] = set()
    dynamic = False

    def read(nodes: Sequence[ast.AST]) -> None:
        nonlocal dynamic
        found, dyn = _scan(nodes, tuple(bound), rebound, dunder=True)
        names.update(found)
        dynamic |= dyn

    def header(node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        args = node.args
        every = (*args.posonlyargs, *args.args, *args.kwonlyargs, args.vararg, args.kwarg)
        read(
            [
                *node.decorator_list,
                *(arg.annotation for arg in every if arg and arg.annotation),
                *(value for value in (*args.defaults, *args.kw_defaults) if value),
                *([node.returns] if node.returns else []),
            ]
        )
        if any(isinstance(item, ast.Lambda) for item in node.decorator_list):
            names.add(node.name)

    def execute(body: Sequence[ast.stmt]) -> None:
        for stmt in body:
            if isinstance(stmt, _FUNCS):
                header(stmt)
            elif isinstance(stmt, ast.ClassDef):
                read([*stmt.decorator_list, *stmt.bases, *stmt.keywords])
                execute(stmt.body)
            elif isinstance(stmt, (ast.With, ast.AsyncWith)):
                read(
                    [item for w in stmt.items for item in (w.context_expr, w.optional_vars) if item]
                )
                execute(stmt.body)
            elif isinstance(stmt, (ast.If, ast.While)):
                read([stmt.test])
                execute(stmt.body)
                execute(stmt.orelse)
            elif isinstance(stmt, (ast.For, ast.AsyncFor)):
                read([stmt.target, stmt.iter])
                execute(stmt.body)
                execute(stmt.orelse)
            elif isinstance(stmt, (ast.Try, ast.TryStar)):
                execute(stmt.body)
                for handler in stmt.handlers:
                    read([handler.type] if handler.type else [])
                    execute(handler.body)
                execute(stmt.orelse)
                execute(stmt.finalbody)
            else:
                read([stmt])

    execute(tree.body)
    return names, dynamic
