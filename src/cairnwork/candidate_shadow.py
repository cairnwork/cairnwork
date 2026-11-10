"""Find nested module files that can shadow imports used by the candidate's tests (#1065)."""

from __future__ import annotations

import ast
import re
import sys
from collections import Counter
from importlib.machinery import all_suffixes
from pathlib import Path

from cairnwork.gitcmd import run_git_bytes

_OID = re.compile(r"^[0-9a-f]{40}$")
_IMPORT_CALLS = frozenset({"import_module", "__import__", "importorskip"})
_ALWAYS_SHADOW = frozenset({"pytest", "_pytest", "pluggy", "conftest", "py"})


def find_shadow_file(
    repo: Path, candidate: str, test_commit: str | None = None, added: frozenset[str] = frozenset()
) -> str | None:
    """Return the first nested candidate file that can shadow an imported module.

    A nested file named like a standard or pytest module always counts. A name some tree or test
    file imports (every dotted part, so a ``__path__`` redirect of ``pkg.helper`` is seen) counts
    when the candidate ``added`` that file or another file has the same module name, so a
    project's own src-layout module (``src/calc.py`` imported as ``calc``) is not one. Names
    compare case-insensitively; any unparseable ``.py`` in the trees fails closed."""
    commits = (candidate,) if test_commit is None else (candidate, test_commit)
    always = {name.casefold() for name in (*sys.stdlib_module_names, *_ALWAYS_SHADOW)}
    imported: set[str] = set()
    for commit in commits:
        _validate_oid(commit)
        for path in _tree_paths(repo, commit):
            if _is_source(path):  # tests and the code they import can both import a shadow
                imported.update(name.casefold() for name in _imports(repo, commit, path))
    paths = _tree_paths(repo, candidate)
    counts = Counter(name for path in paths if (name := _module_name(path)))
    for path in sorted(paths, key=lambda p: p not in added):  # name an added file first
        depth = path.count("/") - (
            path.casefold().endswith("/__init__.py") or path.casefold() == "__init__.py"
        )
        if depth < 1 or not (module := _module_name(path)):  # root modules import as themselves
            continue
        if module in always or (module in imported and (path in added or counts[module] > 1)):
            return path
    return None


def _validate_oid(commit: str) -> None:
    if not _OID.fullmatch(commit):
        raise ValueError("commit must be a full 40-character object id")


def _tree_paths(repo: Path, commit: str) -> tuple[str, ...]:
    raw = run_git_bytes(repo, "ls-tree", "-r", "-z", "--name-only", commit)
    return tuple(
        path for item in raw.split(b"\0") if (path := item.decode("utf-8", "surrogateescape"))
    )


def _is_source(path: str) -> bool:
    return path.casefold().endswith(".py")


def _imports(repo: Path, commit: str, path: str) -> set[str]:
    raw = run_git_bytes(repo, "cat-file", "blob", f"{commit}:{path}")
    try:
        tree = ast.parse(raw, filename=path)
    except (SyntaxError, ValueError, UnicodeError, MemoryError, RecursionError) as exc:
        raise ValueError(f"{commit}:{path} could not be parsed") from exc
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(part for alias in node.names for part in alias.name.split("."))
        elif isinstance(node, ast.ImportFrom):  # relative too: from . import helper
            names.update((node.module or "").split("."))  # a submodule can be redirected
            names.update(alias.name for alias in node.names if alias.name != "*")
        elif isinstance(node, ast.Call) and _call_name(node.func) in _IMPORT_CALLS:
            for arg in node.args[:1]:  # importlib.import_module("x.y"), __import__, importorskip
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    names.update(arg.value.lstrip(".").split("."))
    names.discard("")
    return names


def _call_name(func: ast.expr) -> str:
    return func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")


def _module_name(path: str) -> str | None:
    """The importable name a tree file provides: a module stem or a package directory."""
    name = path.rsplit("/", 1)[-1].casefold()  # compared case-insensitively: fail closed
    if name == "__init__.py":
        parts = path.casefold().split("/")
        return parts[-2] if len(parts) > 1 else None
    for suffix in sorted(all_suffixes(), key=len, reverse=True):  # .py, .pyc, extension modules
        if name.endswith(suffix) and len(name) > len(suffix):
            return name[: -len(suffix)]
    return None
