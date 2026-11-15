"""Narrow same-file inherited-test deletion recognition for P2P collection."""

from __future__ import annotations

import ast


def inherited_same_file_definitions(dropped, base_sources, test_sources):
    """Find deleted inherited methods in unique, same-file, single-base class chains."""

    def cls(tree, name):
        matches = [
            node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == name
        ]
        return matches[0] if len(matches) == 1 else None

    resolved = set()
    for nodeid in dropped:
        path, _, chain = nodeid.partition("::")
        parts = chain.split("::")
        if len(parts) < 2 or path not in base_sources or path not in test_sources:
            continue
        try:
            base, test = ast.parse(base_sources[path]), ast.parse(test_sources[path])
        except (SyntaxError, ValueError, RecursionError):
            continue
        owners, name = parts[:-1], parts[-1].split("[", 1)[0]
        if len(owners) != 1:
            continue
        owner = cls(base, owners[0])
        if owner is None or cls(test, owners[0]) is None:
            continue
        if _methods(owner, name):
            continue
        bases = [node.id for node in owner.bases if isinstance(node, ast.Name)]
        if len(bases) != 1:
            continue
        parent, test_parent = cls(base, bases[0]), cls(test, bases[0])
        if parent is not None and test_parent is not None:
            if len(_methods(parent, name)) == 1 and not _methods(test_parent, name):
                resolved.add(nodeid)
    return resolved


def _methods(owner, name):
    return [
        node
        for node in owner.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
    ]
