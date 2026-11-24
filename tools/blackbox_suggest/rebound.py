"""Bindings of a name in module scope that the top-level reader does not read (#370)."""

import ast

Scopes = ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Lambda
Comprehensions = ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp


def rebound(tree: ast.Module, name: str, read: set[int]) -> bool:
    """Whether name is bound in module scope by a node whose id is not in read: under
    if/try/for/while/with/match, by a walrus, a tuple, for or with target, an import
    statement, an except or match name, a star import, a del, or a global statement in a
    function or class body. Comprehension targets and function, lambda and class bodies are
    their own scopes and are not read."""
    if any(isinstance(n, ast.Global) and name in n.names for n in ast.walk(tree)):
        return True
    local = {
        id(n)
        for comp in ast.walk(tree)
        if isinstance(comp, Comprehensions)
        for gen in comp.generators
        for n in ast.walk(gen.target)
    }
    stack: list[ast.AST] = [tree]
    while stack:
        node = stack.pop()
        if _binds(node, name) and id(node) not in read and id(node) not in local:
            return True
        if isinstance(node, Scopes):
            for field, value in ast.iter_fields(node):
                if field != "body":
                    stack.extend(value if isinstance(value, list) else [value])
            stack[:] = [n for n in stack if isinstance(n, ast.AST)]
        else:
            stack.extend(ast.iter_child_nodes(node))
    return False


def _binds(node: ast.AST, name: str) -> bool:
    """Whether this one node binds name."""
    if isinstance(node, ast.Name):
        return node.id == name and isinstance(node.ctx, ast.Store | ast.Del)
    if isinstance(node, Scopes | ast.ExceptHandler | ast.MatchAs | ast.MatchStar):
        return getattr(node, "name", None) == name
    if isinstance(node, ast.alias):
        return node.name == "*" or (node.asname or node.name.split(".")[0]) == name
    return isinstance(node, ast.MatchMapping) and node.rest == name
