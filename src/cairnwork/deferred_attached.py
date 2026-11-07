"""#994: tests attached outside a class body, which deferred_nodes does not read.

pytest collects a ``test*`` attribute of a test class or a module however it got there. An
*attaching store* puts a callable under a ``test*``/``Test*`` name from outside the class body:
a name bound at module level or in a class body to a name, an attribute, a lambda or a call of
same-file code (``test_x = f``; a function's locals are never collected), an attribute target
(``Cls.test_x = f``, ``sys.modules[__name__].test_x = f``, a ``for`` or ``with`` target too),
``setattr`` (``builtins.setattr``, ``type.__setattr__``), or a
``globals()``/``vars()``/``locals()`` (``builtins.globals()`` too) or ``__dict__`` namespace
written by a subscript or by ``update``/``setdefault``/``__setitem__``. A namespace bound to
another name or handed to a function other than a builtin may be written where it is not
followed: it counts as unreadable. Stores into ``self`` (an instance's own attribute, e.g. a
client set in ``setUp``) and literal values are not counted: pytest collects neither.

Each statement that runs at import (every module-level statement but a plain function, a class
less its method bodies, every function's and method's decorators and defaults, a class body's
own helper that it calls) is searched together with the same-file code it reaches by name
(deferred_nodes._Reach: a class decorator or the class holding it, a metaclass, a base, a helper
it calls; a reached class with every method but its tests). One that holds an attaching store is
*attaching*:
- readable statically (every store names a literal, and its value reads only names the file
  binds by nothing but a module-level ``def`` or ``class``, or builtins): it counts with
  everything it reaches and every other module-level statement, so a changed ``f`` behind an
  unchanged assignment is a change (the owner's trap, 11/07);
- otherwise (a computed name, a value from an import, an assignment, a parameter or a local):
  unreadable; a test commit that changes the file is refused while it is there, changed or not.

Known limit (as #968 (b)): no other module's code is read, so nothing an import brings in is
followed: a class decorator, a metaclass or a base class imported from elsewhere, or an imported
function called at import or while a class is built, attaches nothing here. pytest collects from
``__dict__`` only, so a module-level ``__getattr__``/``__dir__`` cannot add a test. Other dynamic
writes of a namespace (``setattr`` reached through a name, ``exec``) are #1007."""

from __future__ import annotations

import ast
import builtins

from cairnwork.deferred_nodes import _DEFS, _SCOPES, _literal, _Reach
from cairnwork.deferred_overlay import TestCommitRejected

_HOLDERS = frozenset({"globals", "vars", "locals"})
_WRITES = frozenset({"update", "setdefault", "__setitem__"})
_BUILTINS = frozenset(dir(builtins))
_ALIASES = (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.NamedExpr)


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
    """(a digest per readable attaching statement, with what it reaches and every other
    module-level statement; whether the file holds an unreadable one)."""
    reach, plain = _Reach(tree), _plain(tree)
    held: dict[int, list[tuple[str | None, ast.expr]]] = {}  # each reached statement walked once
    found: set[str] = set()
    blind = False
    for stmt in tree.body:
        part = _import_part(stmt)
        if not part:
            continue
        names = frozenset(n.id for x in part for n in ast.walk(x) if isinstance(n, ast.Name))
        stores = _scan(part, plain)
        for other in reach.statements(names):
            if (more := held.get(id(other))) is None:
                parts = _import_part(other, reached=True)
                held[id(other)] = more = _scan(parts, plain)
            stores += more
        if not stores:
            continue
        if all(name is not None and _names(value) <= plain for name, value in stores):
            found.add(reach.module + reach(part))
        else:
            blind = True
    return frozenset(found), blind


def _scan(nodes: list[ast.AST], plain: frozenset[str]) -> list[tuple[str | None, ast.expr]]:
    return [store for node in nodes for store in (*_stores(node), *_name_stores(node, plain))]


def _plain(tree: ast.Module) -> frozenset[str]:
    """The names a readable value may read (Claude r1 on #1003): bound in the file only by a
    module-level ``def`` or ``class``, which reach follows, or a builtin bound nowhere in it. A
    name an assignment, an import, a parameter, a nested ``def`` or ``class`` or a ``global``
    binds anywhere may hold what is not followed (an imported value under another name)."""
    top = {id(stmt) for stmt in tree.body}
    plain = {stmt.name for stmt in tree.body if isinstance(stmt, _SCOPES)} | _BUILTINS
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Load):
            plain.discard(node.id)
        elif isinstance(node, ast.arg):
            plain.discard(node.arg)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            plain -= {(alias.asname or alias.name).split(".")[0] for alias in node.names}
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            plain -= set(node.names)
        elif isinstance(node, _SCOPES) and id(node) not in top:
            plain.discard(node.name)
        elif isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)) and node.name:
            plain.discard(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            plain.discard(node.rest)
    return frozenset(plain)


def _import_part(stmt: ast.stmt, reached: bool = False) -> list[ast.AST]:
    """What of a module-level statement runs at import: a function's decorators and defaults (its
    whole body too once ``reached``, called by what runs), a class's decorators, bases, keywords,
    body less methods and every method's decorators and defaults; a non-reached class also includes
    any non-test method whose name is read there, and a reached class includes every non-test
    method body (Claude r2 and Gemini on #1003). Any other statement is whole. A test class's
    other methods run only when its tests do, after collection."""
    if isinstance(stmt, _DEFS):
        args = stmt.args
        heads: list[ast.AST] = [*stmt.decorator_list, *args.defaults]
        heads += [d for d in args.kw_defaults if d]
        return [stmt] if reached else heads
    if isinstance(stmt, ast.ClassDef):
        body = [m for m in stmt.body if not isinstance(m, _DEFS)]
        methods = [m for m in stmt.body if isinstance(m, _DEFS)]
        heads = [node for method in methods for node in _method_head(method)]
        parts = [*stmt.decorator_list, *stmt.bases, *(k.value for k in stmt.keywords)]
        parts += [*body, *heads]
        if reached:
            parts += [m for m in methods if not _test(m)]
        else:
            reads = {n.id for part in parts for n in ast.walk(part) if isinstance(n, ast.Name)}
            parts += [m for m in methods if not _test(m) and m.name in reads]
        return parts
    return [stmt]


def _method_head(method: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.AST]:
    args = method.args
    return [*method.decorator_list, *args.defaults, *(d for d in args.kw_defaults if d)]


def _stores(node: ast.AST) -> list[tuple[str | None, ast.expr]]:
    """(the ``test*`` name, or None when computed; the value) of each attaching store in
    ``node``, nested functions included."""
    found: list[tuple[str | None, ast.expr]] = []
    for item in ast.walk(node):
        for targets, value in _bindings(item):
            if value is None or _literal(value):
                continue
            if _holder(value) and isinstance(item, _ALIASES):  # ``ns = globals()``: what it
                found.append((None, value))  # writes is not followed; a loop over one only reads
            for target in (t for x in targets for t in ast.walk(x)):
                if (name := _target(target)) != "":
                    found.append((name, value))
        if isinstance(item, ast.Call) and (named := _setattr(item)):
            if (name := _key(named[0])) != "":
                found.append((name, named[1]))
        elif isinstance(item, ast.Call):
            found += [(n, v) for n, v in _call_stores(item) if n != "" and not _literal(v)]
    return found


def _name_stores(node: ast.AST, plain: frozenset[str]) -> list[tuple[str, ast.expr]]:
    """(name, value) of each ``test*`` name bound in ``node`` at module or class-body scope, not
    in a nested scope: pytest never collects a function's locals (Claude r2, Gemini on #1003).
    Only a value that names a callable (a name, an attribute, a lambda) or a call made of
    same-file code counts: pytest collects functions and classes, and data under a ``test*``
    name (``test_data = load(...)``, a list, a path) is common; what an imported call returns
    is not followed, as any other imported code."""
    if isinstance(node, (*_SCOPES, ast.Lambda, ast.comprehension)):
        return []
    found: list[tuple[str, ast.expr]] = []
    pending = [node]
    while pending:
        item = pending.pop()
        if item is not node and isinstance(item, (*_SCOPES, ast.Lambda, ast.comprehension)):
            continue
        for targets, value in _bindings(item):
            if value is None or not _callable(value, plain):
                continue
            for target in (t for x in targets for t in ast.walk(x)):
                if isinstance(target, ast.Name) and isinstance(target.ctx, ast.Store):
                    if _test_name(target.id):
                        found.append((target.id, value))
        pending.extend(ast.iter_child_nodes(item))
    return found


def _callable(value: ast.expr, plain: frozenset[str]) -> bool:
    if isinstance(value, (ast.Name, ast.Attribute, ast.Lambda)):
        return True
    return isinstance(value, ast.Call) and _names(value) <= plain


def _bindings(item: ast.AST) -> list[tuple[list[ast.expr], ast.expr | None]]:
    """(targets, value) of a binding: an assignment or walrus, a ``for`` loop or comprehension (the
    iterable), a ``with`` item (its context manager)."""
    if isinstance(item, ast.Assign):
        return [(item.targets, item.value)]
    if isinstance(item, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr)):
        return [([item.target], item.value)]
    if isinstance(item, (ast.For, ast.AsyncFor, ast.comprehension)):
        return [([item.target], item.iter)]
    if isinstance(item, (ast.With, ast.AsyncWith)):
        return [([w.optional_vars], w.context_expr) for w in item.items if w.optional_vars]
    return []


def _call_stores(call: ast.Call) -> list[tuple[str | None, ast.expr]]:
    """A namespace written by a call (Codex r1 on #1003): ``ns.update(...)``, ``ns.setdefault``
    or ``ns.__setitem__`` with literal names, else computed; a namespace handed to any function
    but a builtin, which may write it, is computed."""
    func = call.func
    if isinstance(func, ast.Attribute) and func.attr in _WRITES and _holder(func.value):
        if func.attr != "update":
            key = _key(call.args[0]) if call.args else None
            return [(key, call.args[1] if len(call.args) > 1 else call)]
        pairs: list[tuple[str | None, ast.expr]] = []
        for arg in call.args:
            if isinstance(arg, ast.Dict):
                items = zip(arg.keys, arg.values, strict=True)  # a ``**`` entry has no key
                pairs += [(_key(k) if k else None, v) for k, v in items]
            else:
                pairs.append((None, arg))
        for word in call.keywords:
            name = word.arg if word.arg is None or _test_name(word.arg) else ""
            pairs.append((name, word.value))
        return pairs
    builtin = isinstance(func, ast.Name) and func.id in _BUILTINS
    given = [*call.args, *(word.value for word in call.keywords)]
    return [] if builtin else [(None, arg) for arg in given if _holder(arg)]


def _target(target: ast.AST) -> str | None:
    """The name an attribute or subscript store attaches; None if computed, "" if none."""
    if isinstance(target, ast.Attribute) and isinstance(target.ctx, ast.Store):
        return target.attr if _test_name(target.attr) and not _is_self(target.value) else ""
    if isinstance(target, ast.Subscript) and isinstance(target.ctx, ast.Store):
        return _key(target.slice) if _holder(target.value) else ""
    return ""


def _setattr(call: ast.Call) -> tuple[ast.expr, ast.expr] | None:
    """(name, value) of ``setattr(obj, name, value)``, ``x.setattr(obj, ...)`` (``builtins``) or
    ``x.__setattr__(obj, ...)`` (``type``, ``object``), or ``obj.__setattr__(name, value)``: a
    value that is not a literal, on anything but ``self``; None for any other call."""
    func, args = call.func, call.args
    word = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
    if call.keywords or word not in ("setattr", "__setattr__"):
        return None
    if isinstance(func, ast.Name) and word == "__setattr__":
        return None
    if len(args) == 2 and word == "__setattr__" and isinstance(func, ast.Attribute):
        args = [func.value, *args]
    if len(args) != 3 or _literal(args[2]) or _is_self(args[0]):
        return None
    return args[1], args[2]


def _holder(value: ast.AST) -> bool:
    """``globals()``, ``vars()``, ``locals()`` or anything's ``__dict__`` but ``self``'s: a
    namespace pytest may collect from."""
    if isinstance(value, ast.Call):
        func = value.func
        if isinstance(func, ast.Name):
            return func.id in _HOLDERS
        if isinstance(func, ast.Attribute):
            return func.attr in _HOLDERS
    own = isinstance(value, ast.Attribute) and _is_self(value.value)
    return isinstance(value, ast.Attribute) and value.attr == "__dict__" and not own


def _is_self(node: ast.AST) -> bool:
    return isinstance(node, ast.Name) and node.id == "self"


def _key(node: ast.expr) -> str | None:
    """A literal name: itself if ``test*``, else ""; None if not a literal."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value if _test_name(node.value) else ""
    return None


def _test_name(name: str) -> bool:
    """pytest's default prefixes, ``testing``/``test_mode`` included: it collects those too."""
    return name.startswith(("test", "Test"))


def _test(method: ast.stmt) -> bool:
    return isinstance(method, _DEFS) and method.name.startswith("test")


def _names(value: ast.expr) -> set[str]:
    return {n.id for n in ast.walk(value) if isinstance(n, ast.Name)}
