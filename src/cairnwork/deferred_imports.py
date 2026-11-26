"""#1833: which module-level names a test file binds by an import and by nothing else."""

import ast
from collections import Counter


def _bound(alias: ast.alias) -> str:
    return (alias.asname or alias.name).split(".")[0]


def imported_names(tree: ast.Module) -> frozenset[str]:
    """Names bound exactly once in the whole file, by a top-level import. Every other binding of
    the name, anywhere (an assignment, ``for``, ``with``, walrus, annotation, ``match`` capture,
    ``global`` write, a def or class, an import in a block or a second import), takes it out, so
    such a base keeps the cautious reading: it may be any earlier class of the file. Names bound
    inside a function count too, which only errs towards the cautious reading."""
    top = {
        _bound(a) for s in tree.body if isinstance(s, (ast.Import, ast.ImportFrom)) for a in s.names
    }
    count: Counter[str] = Counter()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            count.update(_bound(a) for a in node.names)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            count[node.name] += 1
        elif isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Load):
            count[node.id] += 1
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            count.update(node.names)
        elif isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar, ast.MatchMapping)):
            count.update(n for n in (getattr(node, "name", None), getattr(node, "rest", None)) if n)
    return frozenset(name for name in top if count[name] == 1)
