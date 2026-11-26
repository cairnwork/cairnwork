"""#1833: the module-level names a test file binds by an import."""

import ast


def _bound(alias: ast.alias) -> str:
    return (alias.asname or alias.name).split(".")[0]


def imported_names(tree: ast.Module) -> frozenset[str]:
    """Names a top-level import binds (``from m import Base``, ``... as B``, ``import m`` for
    ``m.Base``), except a star import and a name of a function or class the file defines itself.
    The answer only decides whether a class with such a base is also read as a possible test
    case (its own ids are added). It is never used to drop an id, so a wrong answer (the name is
    rebound later, written at run time, or not a class) cannot leave fewer ids than the cautious
    reading gives: no check that the name is untouched is needed or made."""
    local = {
        n.name
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }
    return frozenset(
        _bound(a)
        for s in tree.body
        if isinstance(s, (ast.Import, ast.ImportFrom))
        for a in s.names
        if a.name != "*" and a.name not in local
    )
