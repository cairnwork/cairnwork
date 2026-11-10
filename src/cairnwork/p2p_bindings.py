"""Which statements bind a name in one scope (#847): the base-side half of p2p_collect's
removed/dropped split. Moved out of p2p_collect.py to keep each file under 400 lines."""

from __future__ import annotations

import ast

COMPOUND_STMTS = (
    ast.If,
    ast.Try,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.With,
    ast.AsyncWith,
    ast.Match,
)


def binds_in_scope(node: ast.AST, name: str, *, module: bool = False) -> bool:
    """Whether node binds name in the current scope (does not cross into nested scopes)."""
    stack = [node]
    while stack:
        curr = stack.pop()
        if isinstance(curr, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if curr.name == name:
                return True
            stack.extend(curr.decorator_list)
            if isinstance(curr, (ast.FunctionDef, ast.AsyncFunctionDef)):
                stack.extend(curr.args.defaults)
                stack.extend(d for d in curr.args.kw_defaults if d is not None)
            else:
                stack.extend(curr.bases)
            continue
        if isinstance(
            curr, (ast.Lambda, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
        ):
            continue
        if (
            isinstance(curr, ast.Name)
            and curr.id == name
            and isinstance(curr.ctx, (ast.Store, ast.Del))
        ):
            return True
        if isinstance(curr, ast.Import) and any(
            (a.asname or a.name.partition(".")[0]) == name for a in curr.names
        ):
            return True
        if isinstance(curr, ast.ImportFrom) and any(
            a.name == "*" or (a.asname or a.name) == name for a in curr.names
        ):
            return True
        if not module and isinstance(curr, (ast.Global, ast.Nonlocal)) and name in curr.names:
            return True
        if isinstance(curr, ast.ExceptHandler) and curr.name == name:
            return True
        if isinstance(curr, ast.MatchAs) and curr.name == name:
            return True
        for child in ast.iter_child_nodes(curr):
            stack.append(child)
    return False


def last_binding(body: list[ast.stmt], name: str, *, module: bool = False) -> ast.stmt | None:
    """The last statement in body binding name, or None; ValueError if a compound one binds it."""
    last = None
    for stmt in body:
        in_module_compound = module and isinstance(stmt, COMPOUND_STMTS)
        if isinstance(stmt, COMPOUND_STMTS) and binds_in_scope(
            stmt, name, module=in_module_compound
        ):
            raise ValueError(f"{name} bound in compound statement")
        if binds_in_scope(stmt, name, module=in_module_compound):
            last = stmt
    return last
