"""#1242: a changed test module may not load a changed pytest plugin by name."""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

from cairnwork.conftest_reach import _names
from cairnwork.deferred_overlay import TestCommitRejected
from cairnwork.gitcmd import GitCommandError, run_git_bytes

CODE = "TEST_COMMIT_CONFTEST_STATIC"


def check_test_module_plugins(repo: Path, _base: str, commit: str, paths: tuple[str, ...]) -> None:
    """Refuse module-level pytest_plugins declarations that can load changed files."""
    changed = frozenset(paths)
    if not changed:
        return
    files = _tree_files(repo, commit)
    by_name = _names(files)
    for path in sorted(changed & files):
        if not path.endswith(".py"):
            continue
        tree = _parse(repo, commit, path)
        for node in _module_assignments(tree):
            if not _binds(node):
                continue
            names = _plugin_names(node.value)
            if names is None:
                raise _refusal(path, node, "has a non-literal pytest_plugins value")
            for name in names:
                for target in by_name.get(name, ()):
                    if target in changed:
                        _refuse(path, node, f"loads changed plugin module {name}")
                    stem = target.removesuffix(".py").removesuffix("/__init__")
                    parents = _package_inits(stem)
                    if parents & changed:
                        _refuse(path, node, f"loads plugin module {name} through a changed package")


def _tree_files(repo: Path, commit: str) -> frozenset[str]:
    try:
        raw = run_git_bytes(repo, "ls-tree", "-r", "-z", "--name-only", commit)
    except GitCommandError as exc:
        raise TestCommitRejected("TEST_COMMIT_UNREADABLE", str(exc)) from exc
    return frozenset(raw.decode("utf-8", "surrogateescape").split("\0"))


def _parse(repo: Path, commit: str, path: str) -> ast.Module:
    try:
        raw = run_git_bytes(repo, "cat-file", "blob", f"{commit}:{path}")
        return ast.parse(raw)
    except GitCommandError as exc:
        raise TestCommitRejected("TEST_COMMIT_UNREADABLE", str(exc)) from exc
    except (SyntaxError, ValueError, UnicodeError, MemoryError, RecursionError) as exc:
        raise TestCommitRejected("TEST_COMMIT_UNPARSABLE", path) from exc


def _module_assignments(tree: ast.Module) -> Iterator[ast.Assign | ast.AnnAssign | ast.AugAssign]:
    """Assignments at module scope, including control-flow blocks but excluding functions."""
    todo: list[ast.stmt] = list(tree.body)
    while todo:
        node = todo.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            yield node
        todo.extend(_module_children(node))


def _module_children(node: ast.stmt) -> list[ast.stmt]:
    fields = ("body", "orelse", "finalbody")
    return [
        child
        for field in fields
        for child in getattr(node, field, ())
        if isinstance(child, ast.stmt)
    ]


def _binds(node: ast.Assign | ast.AnnAssign | ast.AugAssign) -> bool:
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    return any(
        isinstance(part, ast.Name) and part.id == "pytest_plugins"
        for target in targets
        for part in ast.walk(target)
    )


def _plugin_names(node: ast.expr | None) -> list[str] | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, (ast.List, ast.Tuple)):
        if all(
            isinstance(item, ast.Constant) and isinstance(item.value, str) for item in node.elts
        ):
            return [item.value for item in node.elts]  # type: ignore[attr-defined]
    return None


def _package_inits(stem: str) -> set[str]:
    parts = stem.split("/")
    return {"/".join(parts[:index] + ["__init__.py"]) for index in range(1, len(parts))}


def _refusal(path: str, node: ast.AST, reason: str) -> TestCommitRejected:
    return TestCommitRejected(CODE, f"{path}:{getattr(node, 'lineno', 1)} {reason}")


def _refuse(path: str, node: ast.AST, reason: str) -> None:
    raise _refusal(path, node, reason)
