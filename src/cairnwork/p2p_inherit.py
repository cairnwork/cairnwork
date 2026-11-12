"""#1128 item 1: a base test a class gets from one same-file base chain, and the class-body
helpers moved out of p2p_collect.py to keep it under 400 lines."""

from __future__ import annotations

import ast

from cairnwork.p2p_bindings import binds_in_scope, is_pytest_decorator, last_binding


def module_mutates_base(tree: ast.Module, name: str) -> bool:
    for stmt in tree.body:
        pending: list[ast.AST] = [stmt]
        while pending:
            node = pending.pop()
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                continue
            if (
                isinstance(node, ast.Attribute)
                and node.attr == "__init_subclass__"
                and isinstance(node.ctx, ast.Store)
                and isinstance(node.value, ast.Name)
                and node.value.id == name
            ):
                return True
            pending.extend(ast.iter_child_nodes(node))
    return False


def class_binds_hook(cls: ast.ClassDef) -> bool:
    return any(binds_in_scope(stmt, "__init_subclass__") for stmt in cls.body)


def mentions_dunder_test(node: ast.AST) -> bool:
    return any(
        (isinstance(n, ast.Name) and n.id == "__test__")
        or (isinstance(n, ast.Constant) and n.value == "__test__")
        or (isinstance(n, ast.arg) and n.arg == "__test__")
        for n in ast.walk(node)
    )


def _plain(cls: ast.ClassDef, tree: ast.Module) -> bool:
    """A class whose tests come only from its own body and its bases: no metaclass or keyword,
    no decorator but pytest's, no ``__init_subclass__`` hook or module-level mutation of it."""
    return (
        not cls.keywords
        and all(map(is_pytest_decorator, cls.decorator_list))
        and not class_binds_hook(cls)
        and not module_mutates_base(tree, cls.name)
    )


def inherited_def(tree: ast.Module, cls: ast.ClassDef, name: str, seen: frozenset = frozenset()):
    """Whether ``cls`` (which does not bind ``name`` itself) gets ``def name`` through a chain of
    single same-file bases, each the only binding of its name. Anything else (several bases, an
    imported or rebound base, a metaclass, a hook) is False: the id stays dropped, never removed."""
    if not _plain(cls, tree) or len(cls.bases) != 1 or not isinstance(cls.bases[0], ast.Name):
        return False
    base = cls.bases[0].id
    classes = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == base]
    bound = [n for n in tree.body if binds_in_scope(n, base, module=True)]
    if base in seen or len(classes) != 1 or bound != classes or not _plain(classes[0], tree):
        return False
    try:
        last = last_binding(classes[0].body, name)
    except ValueError:
        return False
    if last is None:
        return inherited_def(tree, classes[0], name, seen | {base})
    return isinstance(last, (ast.FunctionDef, ast.AsyncFunctionDef)) and last.name == name
