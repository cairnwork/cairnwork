"""Find functions used through imported module and from-import aliases."""

from __future__ import annotations

import ast
from collections.abc import Iterator

from cairnwork.fixture_forms import module_level
from cairnwork.module_imports import import_head, module_import_map


def assignment_callables(tree: ast.Module) -> set[str]:
    words: set[str] = set()
    for stmt in module_level(tree.body):
        if not isinstance(stmt, ast.Assign | ast.AnnAssign | ast.AugAssign):
            continue
        value = getattr(stmt, "value", None)
        if value is None:
            continue
        calls = [node for node in ast.walk(value) if isinstance(node, ast.Call)]
        args = [arg for call in calls for arg in call.args]
        args.extend(keyword.value for call in calls for keyword in call.keywords)
        for arg in args:
            words.update(
                node.id if isinstance(node, ast.Name) else node.attr
                for node in _call_arg_nodes(arg)
                if isinstance(node, ast.Name | ast.Attribute)
            )
    return words


def _call_arg_nodes(node: ast.AST) -> Iterator[ast.AST]:
    if isinstance(node, ast.Lambda):
        return
    yield node
    for child in ast.iter_child_nodes(node):
        yield from _call_arg_nodes(child)


def imported_callable_uses(
    trees: dict[str, ast.Module], index: dict[str, set[str]]
) -> dict[str, set[str]]:
    uses = {file: assignment_callables(tree) for file, tree in trees.items()}
    edges = []
    module_maps = {}
    for file, tree in trees.items():
        module_maps[file] = {}
        for node in module_level(tree.body):
            if isinstance(node, ast.Import | ast.ImportFrom):
                for local, targets in module_import_map(file, node, index, trees.keys()).items():
                    module_maps[file].setdefault(local, set()).update(targets)
            if isinstance(node, ast.ImportFrom):
                head = import_head(file, node.module or "", node.level)
                for target in (index.get(head) or set()) & trees.keys() - {file}:
                    edges.extend((file, target, a.name, a.asname or a.name) for a in node.names)
    for file, tree in trees.items():
        for node in module_level(tree.body):
            if not isinstance(node, ast.Assign | ast.AnnAssign | ast.AugAssign):
                continue
            value = getattr(node, "value", None)
            if value is None:
                continue
            for call in (n for n in ast.walk(value) if isinstance(n, ast.Call)):
                for arg in [*call.args, *(kw.value for kw in call.keywords)]:
                    _record_module_uses(arg, module_maps[file], uses)
    changed = True
    while changed:
        changed = False
        for importer, target, source, local in edges:
            if local in uses[importer] and source not in uses[target]:
                uses[target].add(source)
                changed = True
    return uses


def _record_module_uses(
    arg: ast.AST, mod_map: dict[str, set[str]], uses: dict[str, set[str]]
) -> None:
    def visit(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(node, ast.Attribute) and child is node.value:
                continue
            visit(child)
        if not isinstance(node, ast.Attribute):
            return
        parts = _dotted_parts(node)
        for end in range(len(parts) - 1, 0, -1):
            targets = mod_map.get(".".join(parts[:end]))
            if targets:
                for target in targets:
                    uses[target].add(parts[-1])
                return

    visit(arg)


def _dotted_parts(node: ast.Attribute) -> list[str]:
    parts = []
    current: ast.expr = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    return [current.id, *reversed(parts)] if isinstance(current, ast.Name) else []
