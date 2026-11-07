"""#994: tests attached outside a class body, which deferred_nodes does not read.

pytest collects a ``test*`` attribute of a test class or a module however it got there. An
*attaching store* puts a callable under a ``test*``/``Test*`` name from outside the class body:
an attribute target (``Cls.test_x = f``, ``sys.modules[__name__].test_x = f``), ``setattr`` or a
``globals()``/``vars()``/``locals()``/``__dict__`` subscript. Stores into ``self`` (an instance's
own attribute, e.g. a client set in ``setUp``) and literal values are not counted: pytest
collects neither.

Each statement that runs at import (every module-level statement but a plain function, a class
less its tests, a function's decorators and defaults) is searched together with the same-file
code it reaches by name (deferred_nodes._Reach: a class decorator, a metaclass, a helper it
calls). One that holds an attaching store is *attaching*:
- readable statically (every store names a literal and its value reads only same-file names or
  builtins): it counts with everything it reaches, so a changed ``f`` behind an unchanged
  assignment is a change (the owner's trap, 11/07);
- otherwise (a computed name, a value from an import): unreadable; a test commit that changes the
  file is refused while it is there, changed or not.

Known limit (as #968 (b)): another module's code is not read, so a class decorator or a metaclass
imported from one, or an imported function called at import, attaches nothing here."""

from __future__ import annotations

import ast
import builtins

from cairnwork.deferred_nodes import _DEFS, _binds, _literal, _Reach
from cairnwork.deferred_overlay import TestCommitRejected

_HOLDERS = frozenset({"globals", "vars", "locals"})
_BUILD = frozenset({"__init__", "__new__", "__init_subclass__", "__set_name__", "__prepare__"})
_BUILTINS = frozenset(dir(builtins))


def refuse_attached(path: str, tree: ast.Module, old: ast.Module) -> None:
    """DEFERRED_TESTS_UNREADABLE when ``path``'s attached tests changed or cannot be read."""
    (now, blind), (then, was_blind) = attached(tree), attached(old)
    if blind or was_blind or now != then:
        raise TestCommitRejected(
            "DEFERRED_TESTS_UNREADABLE",
            f"{path}: a test attached outside a class body (an attribute, setattr or namespace"
            " store, or a class decorator or metaclass) changed or cannot be read; Cairnwork"
            " cannot tell which tests it adds, so it cannot check they ran",
        )


def attached(tree: ast.Module) -> tuple[frozenset[str], bool]:
    """(a digest per readable attaching statement, with what it reaches; whether the file holds
    an unreadable one)."""
    reach = _Reach(tree)
    imports = [s for s in tree.body if isinstance(s, (ast.Import, ast.ImportFrom))]
    imported = {name for stmt in imports for name in _binds(stmt)}
    found: set[str] = set()
    blind = False
    for stmt in tree.body:
        part = _import_part(stmt)
        if not part:
            continue
        names = frozenset(n.id for x in part for n in ast.walk(x) if isinstance(n, ast.Name))
        reached = [x for s in reach.statements(names) for x in _import_part(s, reached=True)]
        stores = [store for node in [*part, *reached] for store in _stores(node)]
        if not stores:
            continue
        if all(name is not None and not (_reads(value) & imported) for name, value in stores):
            found.add(reach(part))
        else:
            blind = True
    return frozenset(found), blind


def _import_part(stmt: ast.stmt, reached: bool = False) -> list[ast.AST]:
    """What of a module-level statement runs at import: a function's decorators and defaults (its
    whole body too once ``reached``, called by what runs), a class's decorators, bases, keywords
    and body less its methods (and, ``reached`` as a metaclass or base, the methods Python calls
    while building a class), any other statement whole. A test class's own methods run only
    when its tests do, after collection."""
    if isinstance(stmt, _DEFS):
        args = stmt.args
        heads: list[ast.AST] = [*stmt.decorator_list, *args.defaults]
        heads += [d for d in args.kw_defaults if d]
        return [stmt] if reached else heads
    if isinstance(stmt, ast.ClassDef):
        body = [m for m in stmt.body if not isinstance(m, _DEFS) or reached and m.name in _BUILD]
        return [*stmt.decorator_list, *stmt.bases, *(k.value for k in stmt.keywords), *body]
    return [stmt]


def _stores(node: ast.AST) -> list[tuple[str | None, ast.expr]]:
    """(the ``test*`` name, or None when computed; the value) of each attaching store in
    ``node``, nested functions included."""
    found: list[tuple[str | None, ast.expr]] = []
    for item in ast.walk(node):
        if isinstance(item, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            value = item.value
            if value is None or _literal(value):
                continue
            targets = item.targets if isinstance(item, ast.Assign) else [item.target]
            for target in (t for x in targets for t in ast.walk(x)):
                if (name := _target(target)) != "":
                    found.append((name, value))
        elif isinstance(item, ast.Call) and _setattr(item):
            if (name := _key(item.args[1])) != "":
                found.append((name, item.args[2]))
    return found


def _target(target: ast.AST) -> str | None:
    """The name an attribute or subscript store attaches; None if computed, "" if none."""
    if isinstance(target, ast.Attribute) and isinstance(target.ctx, ast.Store):
        own = isinstance(target.value, ast.Name) and target.value.id == "self"
        return target.attr if _test_name(target.attr) and not own else ""
    if isinstance(target, ast.Subscript) and isinstance(target.ctx, ast.Store):
        return _key(target.slice) if _holder(target.value) else ""
    return ""


def _setattr(call: ast.Call) -> bool:
    """``setattr(obj, name, value)``: a value that is not a literal, on anything but ``self``."""
    plain = isinstance(call.func, ast.Name) and call.func.id == "setattr"
    if not plain or len(call.args) != 3 or call.keywords or _literal(call.args[2]):
        return False
    return not (isinstance(call.args[0], ast.Name) and call.args[0].id == "self")


def _holder(value: ast.expr) -> bool:
    """``globals()``, ``vars()``, ``locals()`` or anything's ``__dict__``: a namespace."""
    if isinstance(value, ast.Call) and isinstance(value.func, ast.Name):
        return value.func.id in _HOLDERS
    return isinstance(value, ast.Attribute) and value.attr == "__dict__"


def _key(node: ast.expr) -> str | None:
    """A literal name: itself if ``test*``, else ""; None if not a literal."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value if _test_name(node.value) else ""
    return None


def _test_name(name: str) -> bool:
    return name.startswith(("test", "Test"))


def _reads(value: ast.expr) -> set[str]:
    """The names a stored value reads that are not builtins."""
    return {n.id for n in ast.walk(value) if isinstance(n, ast.Name)} - _BUILTINS
