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


def _same_bases(test: ast.Module, cls: ast.ClassDef) -> bool:
    found = [n for n in test.body if isinstance(n, ast.ClassDef) and n.name == cls.name]
    # defence in depth: _drops and the test-side _scope_mentions already refuse most re-basings
    return len(found) == 1 and [ast.dump(b) for b in found[0].bases] == [
        ast.dump(b) for b in cls.bases
    ]


def _drops(test: ast.Module, parent: ast.ClassDef, name: str) -> bool:
    """The test commit's copy of ``parent`` no longer binds ``name`` at all."""
    found = [n for n in test.body if isinstance(n, ast.ClassDef) and n.name == parent.name]
    try:
        return len(found) == 1 and last_binding(found[0].body, name) is None
    except ValueError:
        return False


DYNAMIC = {"setattr", "delattr", "globals", "locals", "vars", "type", "__dict__", "_getframe"}
DYNAMIC |= {"currentframe", "f_locals", "__set_name__", "__class_getitem__", "__init_subclass__"}


def _dynamic(tree: ast.Module) -> bool:
    """A file that can rebuild or hide class members at run time (``locals()[...]``, ``setattr``,
    ``type(...)``, ``__dict__``, ``__test__``): the inherited path never trusts its source."""
    return mentions_dunder_test(tree) or any(
        (isinstance(n, ast.Name) and n.id in DYNAMIC)
        or (isinstance(n, ast.Attribute) and n.attr in DYNAMIC)
        for n in ast.walk(tree)
    )


def inherited_def(tree: ast.Module, cls: ast.ClassDef, name: str, test: ast.Module | None):
    """Whether ``cls`` (module level, no binding of ``name`` itself) got ``def name`` through a
    chain of single same-file bases, each the only binding of its name, AND the test commit kept
    every base list of the chain and removed the name from the parent that defined it. Anything
    else (several bases, an imported or rebound base, a metaclass, a hook, a nested class, an
    un-inherited or re-based subclass) is False: the id stays dropped, never removed (#1128)."""
    if test is None or cls not in tree.body:
        return False
    chain = [cls]
    while True:
        cur = chain[-1]
        if not _plain(cur, tree) or len(cur.bases) != 1 or not isinstance(cur.bases[0], ast.Name):
            return False
        base = cur.bases[0].id
        classes = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == base]
        bound = [n for n in tree.body if binds_in_scope(n, base, module=True)]
        if base in {c.name for c in chain} or len(classes) != 1 or bound != classes:
            return False
        if not _plain(classes[0], tree):
            return False
        try:
            last = last_binding(classes[0].body, name)
        except ValueError:
            return False
        chain.append(classes[0])
        if last is not None:
            break
    if not (isinstance(last, (ast.FunctionDef, ast.AsyncFunctionDef)) and last.name == name):
        return False
    if _dynamic(tree) or _dynamic(test):  # #1128 round 4: a rebuilt or hidden parent is dropped
        return False
    return all(_same_bases(test, c) for c in chain[:-1]) and _drops(test, chain[-1], name)
