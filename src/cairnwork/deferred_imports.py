"""#1833: which module-level names a test file binds by an import and by nothing else."""

import ast
from collections import Counter


def _bound(alias: ast.alias) -> str:
    return (alias.asname or alias.name).split(".")[0]


_DEFS = (ast.FunctionDef, ast.AsyncFunctionDef)
_DYNAMIC = {"globals", "vars", "locals", "setattr", "exec", "eval", "__import__", "importlib"}


def _writes_namespace(node: ast.AST) -> bool:
    """A star import, or a name or attribute through which the module namespace may be written
    without a binding statement: no name in the file is then trusted as import-bound."""
    if isinstance(node, ast.ImportFrom):
        return any(a.name == "*" for a in node.names)
    if isinstance(node, ast.Name):
        return node.id in _DYNAMIC or node.id == "__dict__"
    return isinstance(node, ast.Attribute) and node.attr in {"modules", "__dict__", "import_module"}


def imported_names(tree: ast.Module) -> frozenset[str]:
    """Names bound exactly once in the whole file, by a top-level import. Every other binding of
    the name, anywhere (an assignment, ``for``, ``with``, walrus, annotation, ``match`` capture,
    ``global`` write, a def or class, an import in a block or a second import), takes it out, so
    such a base keeps the cautious reading: it may be any earlier class of the file. Names bound
    inside a function count too, which only errs towards the cautious reading. A file with a star
    import, or one that reads ``globals``, ``vars``, ``setattr``, ``exec``, ``eval``,
    ``__import__``, ``importlib``, ``sys.modules`` or ``__dict__``, trusts no name; an import of a
    class the file itself defines (a module importing itself) is not trusted either. Known limit:
    a unittest subclass built at run time is not seen here (tracked in its own Issue)."""
    local = {n.name for n in ast.walk(tree) if isinstance(n, (*_DEFS, ast.ClassDef))}
    top = {  # ``from <this file> import P as Base`` names a class of the file: not import-bound
        _bound(a)
        for s in tree.body
        if isinstance(s, (ast.Import, ast.ImportFrom))
        for a in s.names
        if a.name not in local
    }
    count: Counter[str] = Counter()
    for node in ast.walk(tree):
        if _writes_namespace(node):
            return frozenset()
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            count.update(_bound(a) for a in node.names)
        elif isinstance(node, (*_DEFS, ast.ClassDef)):
            count[node.name] += 1
        elif isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Load):
            count[node.id] += 1
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            count.update(node.names)
        elif isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar, ast.MatchMapping)):
            count.update(n for n in (getattr(node, "name", None), getattr(node, "rest", None)) if n)
    return frozenset(name for name in top if count[name] == 1)
