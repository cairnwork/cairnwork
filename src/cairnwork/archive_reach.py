"""#967: a conftest that puts a run-time-built path on ``sys.path`` can reach an archive the test
commit changes without naming its file (conftest_check.py refuses the named forms).

Refused only when both hold, so that an archive that is only test data passes:

* the conftest changes ``sys.path`` (``sys.path.insert/append/extend``, an assignment or ``+=``
  to ``sys.path`` or an item of it, ``site.addsitedir``); and
* one of its string literals holds an archive suffix pattern (``".zip"``, ``"*.whl"``: a listing
  or a suffix test, not a complete file name), or is the directory of a changed archive (the whole
  path, or its last name).

A pattern is a bare suffix (``".whl"``) or a glob string holding a suffix (``"*.ZIP"``); a dotted
path (``"./vendor/other.zip"``) is one named archive, not a pattern. ``sys.path`` may be reached
through ``import sys as s``, ``from sys import path``, an alias of the list, ``addsitedir``,
``syspath_prepend`` or ``zipimporter``.

Residual (#967): a path built with no such literal (``os.listdir`` of a directory named
elsewhere), an unchanged helper module that changes ``sys.path``, an archive under a name with
no archive suffix (zipimport reads any zip), a path that comes from an environment variable, and
the dynamic ways to reach ``sys.path`` or to spell a suffix.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from posixpath import dirname, normpath

ARCHIVES = (".zip", ".egg", ".whl", ".pyz")  # importable once on sys.path (#924 r6, #967)
_PATH_CALLS = {"insert", "append", "extend", "__iadd__", "__setitem__"}
_PATH_FUNCS = {"addsitedir", "syspath_prepend", "zipimporter"}


def archive_path_reason(source: str, archives: Iterable[str]) -> str:
    """Why ``source`` (a conftest) may import from one of the changed ``archives``; '' if not."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        return ""  # the static check refuses an unparsable conftest elsewhere
    mods, paths = _sys_names(tree)
    if not any(_changes_path(node, mods, paths) for node in ast.walk(tree)):
        return ""
    dirs = {d.lower() for a in archives if (d := dirname(a))}
    dirs |= {d.rsplit("/", 1)[-1] for d in dirs}
    for node in ast.walk(tree):
        value = node.value if isinstance(node, ast.Constant) else None
        if isinstance(value, bytes):
            value = value.decode("latin-1")
        if not isinstance(value, str) or not value:
            continue
        low = value.lower()
        if _pattern(low):
            return f"changes sys.path and names the archive kind {value!r}"
        text = normpath(low).strip("/")
        if text in dirs:
            return f"changes sys.path and names the directory {text} of a changed archive"
    return ""


def _pattern(low: str) -> bool:
    """A listing or suffix pattern: a bare suffix (``.whl``) or a glob string holding one
    (``*.zip``). A dotted path is one complete file name: a literal path to one other archive
    cannot be the changed one (the named forms are the caller's)."""
    if low in ARCHIVES:
        return True
    return any(c in low for c in "*?[") and any(s in low for s in ARCHIVES)


def _sys_names(tree: ast.AST) -> tuple[set[str], set[str]]:
    """Names bound to the ``sys`` module and to the ``sys.path`` list (aliases, from-imports)."""
    mods, paths = {"sys"}, set()
    nodes = list(ast.walk(tree))
    for node in nodes:
        if isinstance(node, ast.Import):
            mods |= {a.asname or a.name for a in node.names if a.name == "sys"}
        elif isinstance(node, ast.ImportFrom) and node.module == "sys":
            paths |= {a.asname or "path" for a in node.names if a.name in ("path", "*")}
    for _ in range(3):  # ``p = sys.path``; ``q = p``
        for node in nodes:
            if isinstance(node, ast.Assign) and _is_sys_path(node.value, mods, paths):
                paths |= {t.id for t in node.targets if isinstance(t, ast.Name)}
    return mods, paths


def _is_sys_path(node: ast.AST, mods: set[str], paths: set[str]) -> bool:
    while isinstance(node, ast.Subscript):
        node = node.value
    if isinstance(node, ast.Name):
        return node.id in paths
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "path"
        and isinstance(node.value, ast.Name)
        and node.value.id in mods
    )


def _changes_path(node: ast.AST, mods: set[str], paths: set[str]) -> bool:
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Name):
            return func.id in _PATH_FUNCS
        if isinstance(func, ast.Attribute):
            return func.attr in _PATH_FUNCS or (
                func.attr in _PATH_CALLS and _is_sys_path(func.value, mods, paths)
            )
    if isinstance(node, ast.Assign):
        return any(_is_sys_path(t, mods, paths) for t in node.targets)
    if isinstance(node, (ast.AugAssign, ast.AnnAssign)):
        return _is_sys_path(node.target, mods, paths)
    return False
