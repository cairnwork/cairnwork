"""#967: a conftest that puts a run-time-built path on ``sys.path`` can reach an archive the test
commit changes without naming its file (conftest_check.py refuses the named forms).

Refused only when both hold, so that an archive that is only test data passes:

* the conftest changes ``sys.path`` (``sys.path.insert/append/extend``, an assignment or ``+=``
  to ``sys.path`` or an item of it, ``site.addsitedir``); and
* one of its string literals holds an archive suffix pattern (``".zip"``, ``"*.whl"``: a listing or a
  suffix test, not a complete file name), or is the directory of a changed archive (the whole path, or its last name).

Residual (#967): a path built with no such literal (``os.listdir`` of a directory named
elsewhere), an unchanged helper module that changes ``sys.path``, an archive under a name with
no archive suffix (zipimport reads any zip), and a path that comes from an environment variable.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from posixpath import dirname

ARCHIVES = (".zip", ".egg", ".whl", ".pyz")
_PATH_CALLS = {"insert", "append", "extend"}


def archive_path_reason(source: str, archives: Iterable[str]) -> str:
    """Why ``source`` (a conftest) may import from one of the changed ``archives``; '' if not."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        return ""  # the static check refuses an unparsable conftest elsewhere
    if not any(_changes_path(node) for node in ast.walk(tree)):
        return ""
    dirs = {d for a in archives if (d := dirname(a))}
    dirs |= {d.rsplit("/", 1)[-1] for d in dirs}
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value.strip("/")
            if _pattern(node.value) and any(s in text.lower() for s in ARCHIVES):
                return f"changes sys.path and names the archive kind {node.value!r}"
            if text and text in dirs:
                return f"changes sys.path and names the directory {text} of a changed archive"
    return ""


def _pattern(text: str) -> bool:
    """A listing or suffix pattern (``*.zip``, ``.whl``), not one complete file name: a literal
    path to one other archive cannot be the changed one (the named forms are the caller's)."""
    return any(c in text for c in "*?[") or text.startswith(".")


def _is_sys_path(node: ast.AST) -> bool:
    while isinstance(node, ast.Subscript):
        node = node.value
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "path"
        and isinstance(node.value, ast.Name)
        and node.value.id == "sys"
    )


def _changes_path(node: ast.AST) -> bool:
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        if node.func.attr == "addsitedir":
            return True
        return node.func.attr in _PATH_CALLS and _is_sys_path(node.func.value)
    if isinstance(node, ast.Assign):
        return any(_is_sys_path(t) for t in node.targets)
    if isinstance(node, (ast.AugAssign, ast.AnnAssign)):
        return _is_sys_path(node.target)
    return False
