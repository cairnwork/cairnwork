"""Static NA rules about the shape of the target and the xunit-style setup around it.

Both are decided from the test module's AST, so they hold on a host with no sandbox backend."""

from __future__ import annotations

import ast
from pathlib import Path

FUNCTION_XUNIT = frozenset(
    {
        "setup_module", "teardown_module", "setup_function", "teardown_function",
        "setup", "teardown", "setUpModule", "tearDownModule",
    }
)  # fmt: skip
# unittest targets: pytest also calls these; unittest's own setUpModule / setUpClass do not count
UNITTEST_MODULE_XUNIT = frozenset(
    {"setup_module", "teardown_module", "setup_function", "teardown_function"}
)
UNITTEST_CLASS_XUNIT = frozenset(
    {"setup_method", "teardown_method", "setup_class", "teardown_class"}
)


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_bytes(), filename=str(path))


def _defs(body: list[ast.stmt]) -> set[str]:
    return {n.name for n in body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def target_shape_na(test_path: Path, name: str) -> bool:
    """The node is ``file::Class``: a class names no single test, a direct call means nothing."""
    if "." in name:
        return False
    body = _tree(test_path).body
    return any(isinstance(n, ast.ClassDef) and n.name == name for n in body) and name not in _defs(
        body
    )


def xunit_setup_na(test_path: Path, name: str) -> bool:
    """pytest runs xunit setup around the target and a direct call does not."""
    body = _tree(test_path).body
    module_level = _defs(body)
    cls_name, _, _ = name.rpartition(".")
    if not cls_name:
        return bool(module_level & FUNCTION_XUNIT)
    if module_level & UNITTEST_MODULE_XUNIT:
        return True
    for node in body:
        if isinstance(node, ast.ClassDef) and node.name == cls_name:
            return bool(_defs(node.body) & UNITTEST_CLASS_XUNIT)
    return False
