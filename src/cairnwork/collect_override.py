"""#960: can the base change what pytest collects, so that "pytest skips this directory" is not
a fact about the base? Read from git objects only (nothing is checked out or run).

The default-skipped directories (``.*``, ``build`` ...) are exempt from the P2P targets only while
pytest really skips them. The base can undo that in three ways this module looks for, and the
caller then refuses (the existing ``P2P_BASE_UNTARGETABLE``) instead of exempting:

* its addopts mention ``norecursedirs`` (``-o norecursedirs=``);
* a tracked ``conftest.py`` defines a collection hook or sets ``pytest_plugins`` at module level;
* a tracked ``conftest.py`` cannot be read as Python, so it may do either.
"""

from __future__ import annotations

import ast
from pathlib import Path

from cairnwork.gitcmd import run_git, run_git_bytes

HOOKS = frozenset({"pytest_ignore_collect", "pytest_collect_directory", "pytest_collect_file"})
_COMPOUND = ("body", "orelse", "finalbody", "handlers")


def _module_level(nodes) -> bool:
    for node in nodes:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name in HOOKS:
            return True
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign | ast.AugAssign):
            targets = [node.target]
        if any(isinstance(t, ast.Name) and t.id == "pytest_plugins" for t in targets):
            return True
        if isinstance(node, ast.If | ast.Try | ast.With | ast.ExceptHandler) and any(
            _module_level(getattr(node, field, [])) for field in _COMPOUND
        ):
            return True
    return False


def _conftests(repo: Path, base_commit: str) -> list[tuple[str, str, str]]:
    rows = run_git(repo, "ls-tree", "-r", "-z", base_commit, "--").split("\0")
    found = []
    for row in filter(None, rows):
        meta, _, path = row.partition("\t")
        mode, kind, oid = meta.split()
        if path.rsplit("/", 1)[-1] == "conftest.py":
            found.append((path, f"{mode} {kind}", oid))
    return found


def changes_collection(repo: Path, base_commit: str, addopts: str) -> bool:
    """True when the base's own files may change what pytest collects (see the module text)."""
    if "norecursedirs" in addopts:
        return True
    for _, kind, oid in _conftests(repo, base_commit):
        if kind not in ("100644 blob", "100755 blob"):
            return True
        try:
            tree = ast.parse(run_git_bytes(repo, "cat-file", "blob", oid))
        except (SyntaxError, ValueError, UnicodeError, MemoryError, RecursionError):
            return True
        if _module_level(tree.body):
            return True
    return False
