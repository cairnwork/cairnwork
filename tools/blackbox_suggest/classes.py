"""How pytest sees a test class, read statically: its marks, methods and bases, following the
bases defined in the same file (Python's method resolution order, pytest's inherited marks)."""

from __future__ import annotations

import ast

from .resolve import dotted


def pytestmark_of(body: list[ast.stmt]) -> list[ast.expr]:
    """The marks of the last ``pytestmark = ...`` (annotated or not): the binding pytest reads."""
    found: list[ast.expr] = []
    for node in body:
        targets = node.targets if isinstance(node, ast.Assign) else []
        targets = [node.target] if isinstance(node, ast.AnnAssign) and node.value else targets
        if any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in targets):
            value: ast.expr = node.value  # type: ignore[union-attr,assignment]
            found = list(value.elts) if isinstance(value, ast.List | ast.Tuple) else [value]
    return found


def is_testcase(node: ast.ClassDef, classes: dict[str, ast.ClassDef], depth: int = 0) -> bool:
    for b in node.bases:
        if (dotted(b) or [""])[-1] == "TestCase":
            return True
        if isinstance(b, ast.Name) and b.id in classes and b.id != node.name and depth < 10:
            if is_testcase(classes[b.id], classes, depth + 1):
                return True
    return False


def unknown_bases(node: ast.ClassDef, classes: dict[str, ast.ClassDef], depth: int = 0) -> bool:
    """A base defined elsewhere, here or through a local base, may bring tests or hooks."""
    for b in node.bases:
        local = classes.get(b.id) if isinstance(b, ast.Name) and b.id != node.name else None
        if local is not None and depth < 10 and not unknown_bases(local, classes, depth + 1):
            continue
        if local is None and (dotted(b) or [""])[-1] in ("object", "TestCase"):
            continue
        return True
    return False


def class_marks(node: ast.ClassDef, classes: dict[str, ast.ClassDef], depth: int = 0) -> list:
    """A class's marks with those of its local bases, which pytest inherits."""
    marks = [*node.decorator_list, *pytestmark_of(node.body)]
    for b in node.bases if depth < 10 else []:
        if isinstance(b, ast.Name) and b.id in classes and b.id != node.name:
            marks += class_marks(classes[b.id], classes, depth + 1)
    return marks


def methods_of(node: ast.ClassDef, classes: dict[str, ast.ClassDef], depth: int = 0) -> dict:
    """Methods as Python resolves them: the class's own, then its bases left to right."""
    found: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
    if depth < 10:
        for b in reversed(node.bases):  # earlier bases win, as in the MRO
            if isinstance(b, ast.Name) and b.id in classes and b.id != node.name:
                found.update(methods_of(classes[b.id], classes, depth + 1))
    for n in node.body:
        if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef):
            found[n.name] = n
    return found
