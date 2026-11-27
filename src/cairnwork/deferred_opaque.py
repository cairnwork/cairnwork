"""#1878: changed test files whose class shape ``test_functions`` cannot read."""

from __future__ import annotations

import ast
from pathlib import Path


def _readable_base(base: ast.expr, seen: set[str]) -> bool:
    if isinstance(base, ast.Name):
        return base.id == "object" or base.id in seen or base.id.endswith("TestCase")
    return (
        isinstance(base, ast.Attribute)
        and base.attr == "TestCase"
        and isinstance(base.value, ast.Name)
        and base.value.id == "unittest"
    )


def opaque_shape(tree: ast.Module) -> bool:
    """#1878: the file has a class shape ``test_functions`` cannot read (allow-list). Readable
    bases are: none, ``object``, a class defined earlier in the file, a plain name ending in
    ``TestCase``, ``unittest.TestCase``. Any other base (module attribute, call, subscript,
    starred), any keyword (``metaclass=``), a class defined inside a function, or any
    three-argument ``type(...)`` call (a class built at run time) is opaque. A changed file with
    such a shape must be named whole by a verifier (deferred_tests.whole_file_required), as no
    node target can be known to cover its changed tests."""
    seen: set[str] = set()
    classes = sorted(
        (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)), key=lambda n: n.lineno
    )
    for node in classes:
        if node.keywords or not all(_readable_base(b, seen) for b in node.bases):
            return True
        seen.add(node.name)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(
            isinstance(n, ast.ClassDef) for n in ast.walk(node)
        ):
            return True
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "type"
            and len(node.args) == 3
        ):
            return True
    return False


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
