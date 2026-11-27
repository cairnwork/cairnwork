"""#1878: changed test files whose class shape ``test_functions`` cannot read."""

from __future__ import annotations

import ast
from pathlib import Path

from cairnwork.deferred_nodes import _SCOPES


def opaque_shape(tree: ast.Module) -> bool:
    """#1878: the file has a class shape ``test_functions`` cannot read: a class whose base is a
    plain name that is no earlier class of the file and does not end in ``TestCase`` (imported or
    aliased base: its tests are unseen), or a module-level three-argument ``type(...)`` call (a
    class built at run time). Attribute bases (``unittest.TestCase``), no base and ``object`` are
    read. A changed file with such a shape must be named whole by a verifier (deferred_tests.
    whole_file_required), as no node target can be known to cover its changed tests."""
    seen: set[str] = set()
    for node in sorted(
        (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)), key=lambda n: n.lineno
    ):
        for base in node.bases:
            if isinstance(base, ast.Name) and base.id != "object":
                if base.id not in seen and not base.id.endswith("TestCase"):
                    return True
        seen.add(node.name)
    return any(
        isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "type"
        and len(n.args) == 3
        for stmt in tree.body
        if not isinstance(stmt, _SCOPES)
        for n in ast.walk(stmt)
    )


def whole_file_required(
    repo: Path, base: str, test_commit: str, paths: tuple[str, ...]
) -> frozenset[str]:
    """#1878: the ``.py`` paths whose test-commit file has a shape ``new_tests`` cannot read
    (``opaque_shape``) and differs from the base file (a missing base file differs): a node target
    cannot be known to cover their changes, so ``untargeted`` wants each named whole."""
    from cairnwork.deferred_tests import _EMPTY, _tree  # circular at import time

    found = set()
    for path in paths:
        if not path.endswith(".py"):
            continue
        tree = _tree(repo, f"{test_commit}:{path}") or _EMPTY
        if opaque_shape(tree):
            old = _tree(repo, f"{base}:{path}") or _EMPTY
            if ast.dump(tree) != ast.dump(old):
                found.add(path)
    return frozenset(found)
