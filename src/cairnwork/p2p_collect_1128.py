"""Recognize only direct, unambiguous same-file inherited-test deletions."""

from __future__ import annotations

import ast


def inherited_same_file_definitions(dropped, base_sources, test_sources):
    """Resolve a direct parent method deletion when test-side code cannot hide/rebind it."""

    def cls(tree, name):
        matches = [
            node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == name
        ]
        return matches[0] if len(matches) == 1 else None

    resolved = set()
    for nodeid in dropped:
        path, _, chain = nodeid.partition("::")
        parts = chain.split("::")
        if len(parts) != 2 or path not in base_sources or path not in test_sources:
            continue
        try:
            base, test = ast.parse(base_sources[path]), ast.parse(test_sources[path])
        except (SyntaxError, ValueError, RecursionError):
            continue
        owner_name, name = parts[0], parts[1].split("[", 1)[0]
        owner, test_owner = cls(base, owner_name), cls(test, owner_name)
        if owner is None or test_owner is None or _methods(owner, name):
            continue
        bases = [node.id for node in owner.bases if isinstance(node, ast.Name)]
        if len(bases) != 1:
            continue
        parent_name = bases[0]
        parent, test_parent = cls(base, parent_name), cls(test, parent_name)
        if parent is None or test_parent is None:
            continue
        if len(_methods(parent, name)) != 1 or _methods(test_parent, name):
            continue
        if _unsafe_test_source(test, name, owner_name, parent_name):
            continue
        if any(_complex_body(item) for item in (test_owner, test_parent)):
            continue
        resolved.add(nodeid)
    return resolved


def _methods(owner, name):
    return [
        node
        for node in owner.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
    ]


def _complex_body(owner):
    return any(
        isinstance(node, (ast.If, ast.Try, ast.ClassDef))
        for statement in owner.body
        for node in ast.walk(statement)
    )


def _unsafe_test_source(tree, name, owner, parent):
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "__test__":
            return True
        if isinstance(node, ast.Attribute) and node.attr == "__test__":
            return True
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return True
        if isinstance(node, ast.Name) and node.id == name:
            return True
        if isinstance(node, ast.Attribute) and node.attr == name:
            return True
        if isinstance(node, ast.arg) and node.arg == name:
            return True
        if isinstance(node, ast.Constant) and node.value == name:
            return True
        if isinstance(node, (ast.Name, ast.Attribute)) and isinstance(node.ctx, ast.Store):
            if isinstance(node, ast.Name) and node.id in {owner, parent, "__test__"}:
                return True
            if isinstance(node, ast.Attribute) and node.attr in {owner, parent, "__test__"}:
                return True
    return False
