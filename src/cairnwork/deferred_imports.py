"""#1833: which module-level names a test file binds by an import and by nothing else."""

import ast
from collections import Counter, defaultdict


def _bound(alias: ast.alias) -> str:
    return (alias.asname or alias.name).split(".")[0]


_DEFS = (ast.FunctionDef, ast.AsyncFunctionDef)
_DYNAMIC = {
    "globals", "vars", "locals", "setattr", "delattr", "exec", "eval", "__import__", "importlib",
    "__dict__", "__globals__", "f_globals", "_getframe", "currentframe", "reload", "import_module",
    "modules",
}  # fmt: skip


def _writes_namespace(node: ast.AST) -> bool:
    """A star import, or a name or attribute through which the module namespace may be written
    without a binding statement: no name in the file is then trusted as import-bound."""
    if isinstance(node, ast.ImportFrom):
        return any(a.name == "*" for a in node.names)
    if isinstance(node, ast.Name):
        return node.id in _DYNAMIC
    return isinstance(node, ast.Attribute) and node.attr in _DYNAMIC


def _named_elsewhere(tree: ast.Module) -> set[str]:
    """Names the file spells out as data, where a run-time write may aim at them: a string (the
    whole text or its last dotted part: ``'Base'``, ``'pkg.Base'``), a keyword argument name
    (``update(Base=P)``), and the attribute of a store or delete (``mod.Base = P``)."""
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            found.add(node.value.rsplit(".", 1)[-1])
        elif isinstance(node, ast.keyword) and node.arg:
            found.add(node.arg)
        elif isinstance(node, ast.Attribute) and not isinstance(node.ctx, ast.Load):
            found.add(node.attr)
    return found


def _needles(tree: ast.Module) -> defaultdict[str, set[str]]:
    """For each imported name, the other names a write could aim at: what it was imported as
    (``from m import Base as B`` is ``Base``), and the attributes a base reads off it."""
    found: defaultdict[str, set[str]] = defaultdict(set)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                found[_bound(a)].update(a.name.split("."))
        elif isinstance(node, ast.ClassDef):
            for base in node.bases:
                attrs = [n.attr for n in ast.walk(base) if isinstance(n, ast.Attribute)]
                roots = [n.id for n in ast.walk(base) if isinstance(n, ast.Name)]
                for root in roots:
                    found[root].update(attrs)
    return found


def _module_writes(tree: ast.Module, modules: set[str]) -> bool:
    """A store or delete on an attribute of a name imported as a module (``import x``)."""
    return any(
        isinstance(n, ast.Attribute)
        and not isinstance(n.ctx, ast.Load)
        and isinstance(n.value, ast.Name)
        and n.value.id in modules
        for n in ast.walk(tree)
    )


def imported_names(tree: ast.Module) -> frozenset[str]:
    """Names bound exactly once in the whole file, by a top-level import. Every other binding of
    the name, anywhere (an assignment, ``for``, ``with``, walrus, annotation, ``match`` capture,
    ``global`` write, a def or class, an import in a block or a second import), takes it out, so
    such a base keeps the cautious reading: it may be any earlier class of the file. Names bound
    inside a function count too, which only errs towards the cautious reading. A file with a star
    import, or one that reads ``globals``, ``vars``, ``setattr``, ``exec``, ``eval``,
    ``__import__``, ``importlib``, ``sys.modules`` or ``__dict__``, trusts no name; an import of a
    class the file itself defines (a module importing itself) is not trusted either. Known limit:
    a unittest subclass built at run time is not seen here (tracked in its own Issue).
    #1840: a name is also not trusted when the file spells it as a string, a keyword argument name
    or the attribute of a store, and nothing is trusted when a module imported by ``import x`` has
    an attribute stored or deleted. These lists are not exhaustive (a write whose target name is
    built at run time and uses none of the names above is not seen); the aim is that the ids read
    for an import-bound base are never fewer than main's cautious reading for such a file."""
    local = {n.name for n in ast.walk(tree) if isinstance(n, (*_DEFS, ast.ClassDef))}
    top = {  # ``from <this file> import P as Base`` names a class of the file: not import-bound
        _bound(a)
        for s in tree.body
        if isinstance(s, (ast.Import, ast.ImportFrom))
        for a in s.names
        if a.name not in local
    }
    modules = {_bound(a) for s in tree.body if isinstance(s, ast.Import) for a in s.names} | {
        _bound(a) for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names
    }
    if _module_writes(tree, modules):
        return frozenset()
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
    spelled = _named_elsewhere(tree)
    needles = _needles(tree)
    return frozenset(
        name for name in top if count[name] == 1 and not ({name} | needles[name]) & spelled
    )
