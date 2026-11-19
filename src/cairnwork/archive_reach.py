"""#967: a conftest that changes ``sys.path`` can reach an archive the test commit changes without
naming its file (conftest_check.py refuses the named forms).

Fail closed: when the test commit changes an archive, a conftest that changes ``sys.path`` in any
detected way is refused, whatever path it puts there. The path text is not judged: a literal can
name a symlink, ``sys.path[0] + '/vendor'`` and ``str(Path.cwd() / 'vendor')`` read as literals but
resolve at run time, so no judgment by text holds. Detected forms: ``sys.path.insert/append/extend``
(and ``__iadd__``, ``__setitem__``), an assignment, ``+=`` or slice assignment to ``sys.path`` or an
item of it, ``site.addsitedir``, ``syspath_prepend``, ``zipimporter`` (also under another name or a
keyword argument). ``sys`` may be reached as ``import sys as s``, ``s = sys``, ``from sys import
path``, an alias of the list, or ``__import__('sys')`` / ``importlib.import_module('sys')`` with
that literal name. A conftest that only reads ``sys.path`` (``sys.path[:]``, ``sys.path[0]``, an
alias that is never changed) is not refused.

Over-refusal (kept, #967): a conftest that only puts its own directory on ``sys.path`` is refused
whenever the test commit changes any archive. Put the archive change in its own commit.

Residual (#967): the dynamic ways to reach ``sys.path`` that are not detected: an alias made by a
walrus, tuple unpacking, a loop variable or a default argument, ``list.insert(sys.path, ...)``,
``os.sys.path``; an unchanged helper module that changes ``sys.path``; an archive under a name with
no archive suffix (zipimport reads any zip).
"""

from __future__ import annotations

import ast
from collections.abc import Iterable

ARCHIVES = (".zip", ".egg", ".whl", ".pyz")  # importable once on sys.path (#924 r6, #967)
_PATH_CALLS = {"insert", "append", "extend", "__iadd__", "__setitem__"}
_PATH_FUNCS = {"addsitedir", "syspath_prepend", "zipimporter"}


def archive_path_reason(source: str, archives: Iterable[str]) -> str:
    """Why ``source`` (a conftest) may import from one of the changed ``archives``; '' if not."""
    if not [a for a in archives if a]:
        return ""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        return ""  # the static check refuses an unparsable conftest elsewhere
    mods, paths = _sys_names(tree)
    if any(_changes_path(node, mods, paths) for node in ast.walk(tree)):
        return "changes sys.path"
    return ""


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
            if node.value is None or isinstance(node.value, ast.Subscript):
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


def _changes_path(node: ast.AST, mods: set[str], paths: set[str]) -> bool:
    """True when this node changes ``sys.path`` (whatever it puts there)."""
    if isinstance(node, ast.Call):
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        return (
            name in _PATH_FUNCS
            or f"{name}()" in paths
            or (
                isinstance(func, ast.Attribute)
                and name in _PATH_CALLS
                and _is_sys_path(func.value, mods, paths)
            )
        )
    if isinstance(node, ast.Assign):
        # a bare name only rebinds; ``sys.path = ...`` / ``sys.path[:] =`` change
        return any(
            not isinstance(t, ast.Name) and _is_sys_path(t, mods, paths) for t in node.targets
        )
    if isinstance(node, ast.AugAssign):
        return _is_sys_path(node.target, mods, paths)
    if isinstance(node, ast.AnnAssign):
        # ``p: list = sys.path`` only binds an alias; ``sys.path: list`` assigns nothing
        return (
            node.value is not None
            and not isinstance(node.target, ast.Name)
            and _is_sys_path(node.target, mods, paths)
        )
    return False
