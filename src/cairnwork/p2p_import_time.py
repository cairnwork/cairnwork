"""Conservative import-time reachability for direct-def test deletions."""

from __future__ import annotations

import ast
from collections.abc import Sequence

_DYNAMIC = {
    "globals",
    "vars",
    "locals",
    "getattr",
    "exec",
    "eval",
    "__import__",
    "importlib",
}
_DYNAMIC_ATTRS = {"modules", "__dict__", "f_globals", "_getframe", "globals"}
_HOOKS = {"__init__", "__set_name__", "__init_subclass__", "__enter__"}


def _import_time_uses_changed(base: ast.Module, test: ast.Module) -> bool:
    """Whether import execution can reach a newly added or changed function definition."""
    old, definitions = _definitions(base), _definitions(test)
    changed = {
        name
        for name, node in definitions.items()
        if name not in old or ast.dump(node) != ast.dump(old[name])
    }
    if not changed:
        return False
    roots, dynamic = _import_reads(test)
    if dynamic:
        return True
    pending, reached = list(roots), set()
    while pending:
        name = pending.pop()
        if name in reached:
            continue
        reached.add(name)
        if name in changed:
            return True
        node = definitions.get(name)
        if node is not None:
            if _dynamic_lookup(node.body):
                return True
            pending.extend(_loaded_names(node.body))
    return False


def _definitions(tree: ast.Module) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    return {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _loaded_names(node: ast.AST | Sequence[ast.AST]) -> set[str]:
    nodes = [node] if isinstance(node, ast.AST) else node
    return {
        child.id
        for item in nodes
        for child in ast.walk(item)
        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load)
    }


_NAME_ONLY = {"list", "sorted", "len", "tuple", "set", "frozenset"}


def _dynamic_lookup(node: ast.AST | Sequence[ast.AST]) -> bool:
    nodes = [node] if isinstance(node, ast.AST) else node
    for item in nodes:
        name_only = {
            id(arg)
            for call in ast.walk(item)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id in _NAME_ONLY
            for arg in call.args
        }
        for child in ast.walk(item):
            if isinstance(child, ast.Name) and child.id in _DYNAMIC:
                return True
            if (
                isinstance(child, ast.Attribute)
                and child.attr in _DYNAMIC_ATTRS
                and not (child.attr == "__dict__" and id(child) in name_only)
            ):
                return True
    return False


def _import_reads(tree: ast.Module) -> tuple[set[str], bool]:
    roots: set[str] = set()
    dynamic = False
    classes = {node.name: node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)}

    def read(node: ast.AST | Sequence[ast.AST]) -> None:
        nonlocal dynamic
        nodes = [node] if isinstance(node, ast.AST) else node
        roots.update(_loaded_names(nodes))
        dynamic |= _dynamic_lookup(nodes)
        for item in nodes:
            for child in ast.walk(item):
                if isinstance(child, ast.Call) and isinstance(child.func, ast.Name):
                    cls = classes.get(child.func.id)
                    if cls:
                        roots.update(_class_hooks(cls, classes))
                if isinstance(child, ast.Call) and isinstance(child.func, ast.Lambda):
                    roots.update(_loaded_names(child.func.body))

    def header(node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        read(node.decorator_list)
        if any(
            isinstance(decorator, ast.Lambda)
            or isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Lambda)
            for decorator in node.decorator_list
        ):
            roots.add(node.name)
        for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs):
            if arg.annotation:
                read(arg.annotation)
        if node.args.vararg and node.args.vararg.annotation:
            read(node.args.vararg.annotation)
        if node.args.kwarg and node.args.kwarg.annotation:
            read(node.args.kwarg.annotation)
        read([value for value in (*node.args.defaults, *node.args.kw_defaults) if value])
        if node.returns:
            read(node.returns)

    def execute(body: list[ast.stmt]) -> None:
        for stmt in body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                header(stmt)
            elif isinstance(stmt, ast.ClassDef):
                read([*stmt.decorator_list, *stmt.bases, *stmt.keywords])
                for base in stmt.bases:
                    if isinstance(base, ast.Name):
                        roots.update(
                            _class_hooks(classes.get(base.id), classes) & {"__init_subclass__"}
                        )
                for keyword in stmt.keywords:
                    if keyword.arg == "metaclass" and isinstance(keyword.value, ast.Name):
                        roots.update(
                            _class_hooks(classes.get(keyword.value.id), classes) & {"__init__"}
                        )
                for item in stmt.body:
                    if isinstance(item, (ast.Assign, ast.AnnAssign)):
                        value = item.value
                        if isinstance(value, ast.Call) and isinstance(value.func, ast.Name):
                            roots.update(_class_hooks(classes.get(value.func.id), classes))
                execute(stmt.body)
            elif isinstance(stmt, (ast.With, ast.AsyncWith)):
                read([item.context_expr for item in stmt.items])
                for item in stmt.items:
                    call = item.context_expr
                    if isinstance(call, ast.Call) and isinstance(call.func, ast.Name):
                        cls = classes.get(call.func.id)
                        if cls:
                            roots.add("__enter__")
                            roots.update(_class_hooks(cls, classes))
                execute(stmt.body)
            else:
                read(stmt)
                for child in ast.iter_child_nodes(stmt):
                    if isinstance(child, (ast.If, ast.Try, ast.For, ast.While)):
                        execute(child.body)
                        execute(getattr(child, "orelse", []))

    execute(tree.body)
    return roots, dynamic


def _class_hooks(cls: ast.ClassDef | None, classes: dict[str, ast.ClassDef]) -> set[str]:
    if cls is None:
        return set()
    names = {
        node.name for node in cls.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    hooks = names & _HOOKS
    for base in cls.bases:
        if isinstance(base, ast.Name):
            hooks.update(_class_hooks(classes.get(base.id), classes))
    for keyword in cls.keywords:
        if keyword.arg == "metaclass" and isinstance(keyword.value, ast.Name):
            hooks.update(_class_hooks(classes.get(keyword.value.id), classes) & {"__init__"})
    return hooks
