"""Conservative import-time reachability for direct-def test deletions."""

from __future__ import annotations

import ast

_DYNAMIC = {"globals", "vars", "getattr", "exec", "eval", "__import__", "importlib"}


def _import_time_uses_changed(base: ast.Module, test: ast.Module) -> bool:
    """Whether import execution can reach a newly added or changed function definition."""
    old = _definitions(base)
    definitions = _definitions(test)
    changed = {
        name
        for key, (name, node) in definitions.items()
        if key not in old or ast.dump(node) != ast.dump(old[key][1])
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
        for node in definitions.values():
            if node[0] == name:
                if _dynamic_lookup(node[1].body):
                    return True
                pending.extend(_loaded_names(node[1].body))
    return False


def _definitions(tree: ast.Module) -> dict[tuple[str, ...], tuple[str, ast.AST]]:
    result = {}

    def visit(body, scope=()):
        for stmt in body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                key = (*scope, stmt.name)
                result[key] = (stmt.name, stmt)
                visit(stmt.body, key)
            elif isinstance(stmt, ast.ClassDef):
                visit(stmt.body, (*scope, stmt.name))

    visit(tree.body)
    return result


def _loaded_names(node: ast.AST | list[ast.AST]) -> set[str]:
    nodes = [node] if isinstance(node, ast.AST) else node
    return {
        n.id if isinstance(n, ast.Name) else n.attr
        for item in nodes
        for n in ast.walk(item)
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) or isinstance(n, ast.Attribute)
    }


def _dynamic_lookup(node: ast.AST | list[ast.AST]) -> bool:
    nodes = [node] if isinstance(node, ast.AST) else node
    return any(
        isinstance(n, ast.Name)
        and n.id in _DYNAMIC
        or isinstance(n, ast.Attribute)
        and n.attr in {"modules", "__dict__"}
        for item in nodes
        for n in ast.walk(item)
    )


def _import_reads(tree: ast.Module) -> tuple[set[str], bool]:
    roots, dynamic = set(), False

    def read(node):
        nonlocal dynamic
        nodes = [node] if isinstance(node, ast.AST) else node
        for item in nodes:
            roots.update(_loaded_names(item))
        dynamic |= any(_dynamic_lookup(item) for item in nodes)

    def header(node):
        for expr in [*node.decorator_list, *getattr(node, "bases", ())]:
            read(expr)
        for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs):
            if arg.annotation:
                read(arg.annotation)
        if node.args.vararg and node.args.vararg.annotation:
            read(node.args.vararg.annotation)
        if node.args.kwarg and node.args.kwarg.annotation:
            read(node.args.kwarg.annotation)
        for value in (*node.args.defaults, *node.args.kw_defaults):
            if value:
                read(value)
        if node.returns:
            read(node.returns)

    def execute(body):
        for stmt in body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                header(stmt)
            elif isinstance(stmt, ast.ClassDef):
                read(stmt.decorator_list)
                read(stmt.bases)
                read(stmt.keywords)
                execute(stmt.body)
            else:
                read(stmt)

    execute(tree.body)
    return roots, dynamic
