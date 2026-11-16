"""#967: a conftest that puts a run-time-built path on ``sys.path`` can reach an archive the test
commit changes without naming its file (conftest_check.py refuses the named forms).

Refused only when the conftest changes ``sys.path`` (``sys.path.insert/append/extend``, an
assignment or ``+=`` to ``sys.path`` or an item of it, ``site.addsitedir``, ``syspath_prepend``,
``zipimporter``) and a path it puts there may resolve to a changed archive, so that an archive that
is only test data passes. Judged on the expressions that go onto ``sys.path`` (not the whole file):

* an expression built at run time (a variable, a listing or glob call, a comprehension) cannot be
  judged: refused;
* a literal path is refused if it is a bare suffix (``".whl"``), holds a glob character, or is the
  directory of a changed archive (the whole path, or its last name, any case).

``sys`` may be reached as ``import sys as s``, ``s = sys``, ``from sys import path``, an alias of
the list, or ``__import__('sys')`` / ``importlib.import_module('sys')`` with that literal name.

Over-refusal (kept, #967): a path built from a variable, ``Path(...)`` of a variable, or a listing
call is refused whenever the test commit changes an archive, even if it never reaches it.

Residual (#967): a path built with no such literal (``os.listdir`` of a directory named
elsewhere, then put on ``sys.path`` through a helper), an unchanged helper module that changes
``sys.path``, an archive under a name with no archive suffix (zipimport reads any zip), a path
that comes from an environment variable, and the dynamic ways to reach ``sys.path``: an alias made
by a walrus, tuple unpacking, a loop variable or a default argument,
``list.insert(sys.path, ...)``, ``os.sys.path``, and a suffix test written as a regular expression.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from posixpath import dirname, normpath

ARCHIVES = (".zip", ".egg", ".whl", ".pyz")  # importable once on sys.path (#924 r6, #967)
_PATH_CALLS = {"insert", "append", "extend", "__iadd__", "__setitem__"}
_PATH_FUNCS = {"addsitedir", "syspath_prepend", "zipimporter"}
_LISTING = {"listdir", "scandir", "glob", "iglob", "rglob", "iterdir", "walk", "fwalk"}
_PURE_BASES = {"os", "posixpath", "ntpath", "pathlib", "glob", "Path", "PurePath", "str", "sys"}
_PURE_FUNCS = {"Path", "PurePath", "PurePosixPath", "str", "abspath", "join", "dirname", "normpath"}


def archive_path_reason(source: str, archives: Iterable[str]) -> str:
    """Why ``source`` (a conftest) may import from one of the changed ``archives``; '' if not."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        return ""  # the static check refuses an unparsable conftest elsewhere
    mods, paths = _sys_names(tree)
    exprs = [e for node in ast.walk(tree) for e in _path_args(node, mods, paths)]
    dirs = {d.lower() for a in archives if (d := dirname(a))}
    dirs |= {d.rsplit("/", 1)[-1] for d in dirs}
    for expr in exprs:
        if not _literal(expr, mods, paths):
            return "puts a path built at run time on sys.path"
        for node in ast.walk(expr):
            value = node.value if isinstance(node, ast.Constant) else None
            if isinstance(value, bytes):
                value = value.decode("latin-1")
            if not isinstance(value, str) or not value:
                continue
            low = value.lower()
            if _pattern(low):
                return f"puts the archive kind {value!r} on sys.path"
            if normpath(low).strip("/") in dirs:
                return f"puts the directory {value!r} of a changed archive on sys.path"
    return ""


def _pattern(low: str) -> bool:
    """A suffix or listing pattern: a bare suffix (``.whl``) or any glob string. A dotted path
    (``./vendor/other.zip``) is one complete file name: a literal path to one other archive
    cannot be the changed one (the named forms are the caller's)."""
    return low in ARCHIVES or any(c in low for c in "*?[")


def _is_sys_mod(node: ast.AST, mods: set[str]) -> bool:
    """``sys``, a name bound to it, or ``__import__('sys')`` / ``import_module('sys')``."""
    if isinstance(node, ast.Name):
        return node.id in mods
    if not isinstance(node, ast.Call) or not node.args:
        return False
    func, arg = node.func, node.args[0]
    name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
    return (
        name in ("__import__", "import_module")
        and isinstance(arg, ast.Constant)
        and arg.value == "sys"
    )


def _sys_names(tree: ast.AST) -> tuple[set[str], set[str]]:
    """Names bound to the ``sys`` module and to the ``sys.path`` list (aliases, from-imports)."""
    mods, paths = {"sys"}, set()
    nodes = list(ast.walk(tree))
    for node in nodes:
        if isinstance(node, ast.Import):
            mods |= {a.asname or a.name for a in node.names if a.name == "sys"}
        elif isinstance(node, ast.ImportFrom) and node.module == "sys":
            paths |= {a.asname or "path" for a in node.names if a.name in ("path", "*")}
        elif isinstance(node, ast.ImportFrom) and node.module in ("site", "zipimport"):
            # a path-changing function under another name: kept in ``paths`` as ``name()``
            paths |= {f"{a.asname or a.name}()" for a in node.names if a.name in _PATH_FUNCS}
    for _ in range(4):  # ``s = sys``; ``t = s``; ``p = s.path``; ``q = p``
        for node in nodes:
            if isinstance(node, ast.AnnAssign) and node.value is not None:
                targets = [node.target]
            elif isinstance(node, ast.Assign):
                targets = node.targets
            else:
                continue
            names = {t.id for t in targets if isinstance(t, ast.Name)}
            if isinstance(node.value, ast.Subscript):
                continue  # a copy (``sys.path[:]``) or an item (``sys.path[0]``), not the list
            if _is_sys_mod(node.value, mods):
                mods |= names
            elif _is_sys_path(node.value, mods, paths):
                paths |= names
    return mods, paths


def _is_sys_path(node: ast.AST, mods: set[str], paths: set[str]) -> bool:
    while isinstance(node, ast.Subscript):
        node = node.value
    if isinstance(node, ast.Name):
        return node.id in paths
    return isinstance(node, ast.Attribute) and node.attr == "path" and _is_sys_mod(node.value, mods)


def _path_args(node: ast.AST, mods: set[str], paths: set[str]) -> list[ast.expr]:
    """The expressions this node puts on ``sys.path`` (empty if it does not change it)."""
    if isinstance(node, ast.Call):
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        hit = (
            name in _PATH_FUNCS
            or f"{name}()" in paths
            or (
                isinstance(func, ast.Attribute)
                and name in _PATH_CALLS
                and _is_sys_path(func.value, mods, paths)
            )
        )
        return [*node.args, *(k.value for k in node.keywords)] if hit else []
    if isinstance(node, ast.Assign) and any(
        not isinstance(t, ast.Name) and _is_sys_path(t, mods, paths) for t in node.targets
    ):
        return [
            node.value
        ]  # a bare name only rebinds; ``sys.path = ...`` / ``sys.path[:] =`` change
    if isinstance(node, (ast.AugAssign, ast.AnnAssign)) and _is_sys_path(node.target, mods, paths):
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            return []  # ``p: list = sys.path`` only binds an alias
        return [node.value] if node.value is not None else []
    return []


def _literal(node: ast.AST, mods: set[str], paths: set[str]) -> bool:
    """True when ``node`` is built from literals alone (``__file__`` and path joins included):
    no variable, no listing or glob call, no comprehension. Such a path can be judged by name."""
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, ast.Name):
        return node.id == "__file__"
    if isinstance(node, ast.Attribute):
        return _is_sys_path(node, mods, paths) or _literal(node.value, mods, paths)
    if isinstance(node, ast.Call):
        return _pure_func(node.func) and all(
            _literal(a, mods, paths) for a in [*node.args, *(k.value for k in node.keywords)]
        )
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return all(_literal(e, mods, paths) for e in node.elts)
    if isinstance(node, ast.BinOp):
        return _literal(node.left, mods, paths) and _literal(node.right, mods, paths)
    if isinstance(node, ast.Subscript):
        return _literal(node.value, mods, paths) and _literal(node.slice, mods, paths)
    if isinstance(node, ast.Slice):
        return all(_literal(p, mods, paths) for p in (node.lower, node.upper, node.step) if p)
    if isinstance(node, ast.Starred):
        return _literal(node.value, mods, paths)
    return False


def _pure_func(func: ast.AST) -> bool:
    """A call target that builds a path by name (``os.path.join``, ``Path``), not a lister."""
    if isinstance(func, ast.Name):
        return func.id in _PURE_FUNCS
    if not isinstance(func, ast.Attribute) or func.attr in _LISTING:
        return False
    base = func.value
    while isinstance(base, ast.Attribute):
        if base.attr in _LISTING:
            return False
        base = base.value
    if isinstance(base, ast.Name):
        return base.id in _PURE_BASES
    return isinstance(base, ast.Call) and _pure_func(base.func)
