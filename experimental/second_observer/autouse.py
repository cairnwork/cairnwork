"""Static scan for autouse fixtures the bare observer would not run (AC7 extension).

The observer runs no conftest, so an autouse fixture in a conftest.py on the way from the
rootdir to the test module, or in a module those load through ``pytest_plugins``, makes a direct
call not equivalent to pytest's. The scan is AST only: a fixture registered dynamically (a
decorator built at run time, ``request.getfixturevalue``, a plugin loaded by hook) is NOT seen.
That is a known limit."""

from __future__ import annotations

import ast
from pathlib import Path


def _is_autouse_fixture(node: ast.AST) -> bool:
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return False
    for dec in node.decorator_list:
        if not isinstance(dec, ast.Call):
            continue
        target = (
            dec.func.attr if isinstance(dec.func, ast.Attribute) else getattr(dec.func, "id", "")
        )
        for kw in dec.keywords:
            # anything but a literal False counts: a computed value may be true
            if target == "fixture" and kw.arg == "autouse":
                if not (isinstance(kw.value, ast.Constant) and kw.value.value is False):
                    return True
    return False


def _plugin_names(tree: ast.Module) -> list[str]:
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "pytest_plugins" for t in node.targets
        ):
            value = node.value
            items = value.elts if isinstance(value, (ast.List, ast.Tuple)) else [value]
            return [
                i.value for i in items if isinstance(i, ast.Constant) and isinstance(i.value, str)
            ]
    return []


def _module_file(name: str, roots: list[Path]) -> Path | None:
    for root in roots:
        for rel in (
            Path(*name.split(".")).with_suffix(".py"),
            Path(*name.split(".")) / "__init__.py",
        ):
            if (root / rel).is_file():
                return root / rel
    return None


def autouse_files(checkout: Path, test_file: str, extra_roots: list[Path]) -> list[str]:
    """Checkout-relative files (conftests, test module, plugin modules) defining an autouse fixture.
    Raises SyntaxError/OSError when a file cannot be read: the caller reports OBS_ERROR."""
    checkout = checkout.resolve()
    test_path = checkout / test_file
    queue = [checkout / "conftest.py"]
    directory = checkout
    for part in Path(test_file).parent.parts:
        directory = directory / part
        queue.append(directory / "conftest.py")
    queue.append(test_path)
    roots, seen, hits = [checkout, *extra_roots], set(), []
    while queue:
        path = queue.pop(0)
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        tree = ast.parse(path.read_bytes(), filename=str(path))
        if any(_is_autouse_fixture(n) for n in ast.walk(tree)):
            hits.append(str(path.relative_to(checkout)))
        for name in _plugin_names(tree):
            found = _module_file(name, roots)
            if found is not None and found.resolve().is_relative_to(checkout):
                queue.append(found.resolve())
    return sorted(hits)
