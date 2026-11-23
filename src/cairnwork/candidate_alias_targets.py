"""Where Python binds a name without an assignment statement (for, with, case, parameters)."""

from __future__ import annotations

import ast


def target_binding_sources(node: ast.AST) -> list[ast.AST]:
    """Expressions that flow into a name bound by ``node`` other than through ``=``: a loop or
    comprehension iterable, a ``with ... as`` context, a ``match`` subject, a parameter default."""
    if isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
        return [node.iter]
    if isinstance(node, ast.withitem):
        return [node.context_expr] if node.optional_vars is not None else []
    if isinstance(node, ast.Match):
        return [node.subject]
    if isinstance(node, ast.arguments):
        return [*node.defaults, *(d for d in node.kw_defaults if d is not None)]
    return []


def target_binding_values(node: ast.AST) -> list[ast.AST]:
    """Value positions inside the sources: a name that only appears as the function of a call
    (``open`` in ``open('x')``) is called, not carried into the bound name, so it is skipped."""
    values: list[ast.AST] = []
    for source in target_binding_sources(node):
        called = {id(part.func) for part in ast.walk(source) if isinstance(part, ast.Call)}
        values.extend(part for part in ast.walk(source) if id(part) not in called)
    return values
