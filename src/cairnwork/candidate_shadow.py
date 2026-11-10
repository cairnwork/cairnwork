"""Find nested module files that can shadow imports used by the candidate's tests (#1065)."""

from __future__ import annotations

import ast
import re
import sys
from importlib.machinery import all_suffixes
from pathlib import Path

from cairnwork.gitcmd import run_git_bytes

_OID = re.compile(r"^[0-9a-f]{40}$")
_ALWAYS_SHADOW = frozenset({"pytest", "_pytest", "pluggy", "conftest", "py"})


def find_shadow_file(repo: Path, candidate: str, test_commit: str | None = None) -> str | None:
    """Return the first nested candidate file shadowing a standard or test-imported module."""
    commits = (candidate,) if test_commit is None else (candidate, test_commit)
    names = set(sys.stdlib_module_names) | set(_ALWAYS_SHADOW)
    for commit in commits:
        _validate_oid(commit)
        paths = _tree_paths(repo, commit)
        for path in paths:
            if _is_test(path):
                names.update(_test_imports(repo, commit, path))
    for path in _tree_paths(repo, candidate):
        if module := _nested_module(path):
            if module in names:
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


def _is_test(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return (
        name == "conftest.py"
        or name.startswith("test_")
        and name.endswith(".py")
        or name.endswith("_test.py")
    )


def _test_imports(repo: Path, commit: str, path: str) -> set[str]:
    raw = run_git_bytes(repo, "cat-file", "blob", f"{commit}:{path}")
    try:
        tree = ast.parse(raw, filename=path)
    except (SyntaxError, ValueError, UnicodeError, MemoryError, RecursionError) as exc:
        raise ValueError(f"{commit}:{path} could not be parsed") from exc
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names.add(node.module.split(".", 1)[0])
    return names


def _nested_module(path: str) -> str | None:
    if "/" not in path:
        return None
    name = path.rsplit("/", 1)[-1]
    if name == "__init__.py":
        parts = path.split("/")
        return parts[-2] if len(parts) > 2 else None
    for suffix in sorted(all_suffixes(), key=len, reverse=True):  # .py, .pyc, extension modules
        if name.endswith(suffix) and len(name) > len(suffix):
            return name[: -len(suffix)]
    return None
