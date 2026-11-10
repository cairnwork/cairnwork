"""Which statements bind a name in one scope (#847): the base-side half of p2p_collect's
removed/dropped split. Moved out of p2p_collect.py to keep each file under 400 lines."""

from __future__ import annotations

import ast
import re
from collections import Counter

COMPOUND_STMTS = (
    ast.If,
    ast.Try,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.With,
    ast.AsyncWith,
    ast.Match,
)


def walk_without_nested_classes(node: ast.AST):
    """Walk a statement without entering any class nested inside it."""
    pending = [node]
    while pending:
        current = pending.pop()
        yield current
        if isinstance(current, ast.ClassDef):
            pending.extend(
                item
                for field in ("decorator_list", "bases", "keywords", "type_params")
                for item in getattr(current, field, ())
            )
            if nested_class_body_is_dynamic(current):
                pending.extend(current.body)
            continue
        pending.extend(ast.iter_child_nodes(current))  # a nested ClassDef is yielded, then pruned


def nested_class_body_is_dynamic(node: ast.ClassDef) -> bool:
    """Whether class-body code can inspect or mutate its enclosing namespace."""
    dynamic_names = {
        "_getframe",
        "currentframe",
        "f_locals",
        "setattr",
        "locals",
        "vars",
        "globals",
        "__dict__",
        "__set_name__",
        "__init_subclass__",
        "__class_getitem__",
    }
    dynamic_strings = dynamic_names | {"inspect", "sys"}
    return any(
        (isinstance(item, ast.Name) and item.id in dynamic_names)
        or (isinstance(item, ast.Attribute) and item.attr in dynamic_names)
        or (isinstance(item, ast.Constant) and item.value in dynamic_strings)
        for statement in node.body
        for item in ast.walk(statement)
    )


def mentions_ast(node: ast.AST, name: str, pattern: re.Pattern, defs: bool = False) -> bool:
    defined = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
    if defs and isinstance(node, defined) and node.name == name:
        return True
    if isinstance(node, ast.Name) and node.id == name:
        return True
    if isinstance(node, ast.alias):
        return (node.asname or node.name.split(".")[-1]) == name
    if isinstance(node, ast.Attribute) and node.attr == name:
        return True
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and bool(pattern.search(node.value))
    )


def binds_in_scope(node: ast.AST, name: str, *, module: bool = False) -> bool:
    """Whether node binds name in the current scope (does not cross into nested scopes)."""
    stack = [node]
    while stack:
        curr = stack.pop()
        if isinstance(curr, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if curr.name == name:
                return True
            stack.extend(curr.decorator_list)
            if isinstance(curr, (ast.FunctionDef, ast.AsyncFunctionDef)):
                stack.extend(curr.args.defaults)
                stack.extend(d for d in curr.args.kw_defaults if d is not None)
            else:
                stack.extend(curr.bases)
                stack.extend(kw.value for kw in curr.keywords)
            continue
        if isinstance(curr, ast.Lambda):
            stack.extend(curr.args.defaults)
            stack.extend(d for d in curr.args.kw_defaults if d is not None)
            continue
        if isinstance(curr, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            if curr.generators:
                stack.append(curr.generators[0].iter)
            if isinstance(curr, ast.DictComp):
                stack.extend((curr.key, curr.value))
            else:
                stack.append(curr.elt)
            for generator in curr.generators:
                stack.extend(generator.ifs)
            continue
        if (
            isinstance(curr, ast.Name)
            and curr.id == name
            and isinstance(curr.ctx, (ast.Store, ast.Del))
        ):
            return True
        if isinstance(curr, ast.Import) and any(
            (a.asname or a.name.partition(".")[0]) == name for a in curr.names
        ):
            return True
        if isinstance(curr, ast.ImportFrom) and any(
            a.name == "*" or (a.asname or a.name) == name for a in curr.names
        ):
            return True
        if not module and isinstance(curr, (ast.Global, ast.Nonlocal)) and name in curr.names:
            return True
        if isinstance(curr, ast.ExceptHandler) and curr.name == name:
            return True
        if isinstance(curr, ast.MatchAs) and curr.name == name:
            return True
        if isinstance(curr, ast.MatchStar) and curr.name == name:
            return True
        if isinstance(curr, ast.MatchMapping) and curr.rest == name:
            return True
        for child in ast.iter_child_nodes(curr):
            stack.append(child)
    return False


def last_binding(body: list[ast.stmt], name: str, *, module: bool = False) -> ast.stmt | None:
    """The last statement in body binding name, or None; ValueError if a compound one binds it."""
    last = None
    for stmt in body:
        in_module_compound = module and isinstance(stmt, COMPOUND_STMTS)
        if isinstance(stmt, COMPOUND_STMTS) and binds_in_scope(
            stmt, name, module=in_module_compound
        ):
            raise ValueError(f"{name} bound in compound statement")
        if binds_in_scope(stmt, name, module=in_module_compound):
            last = stmt
    return last


Shadow = tuple[list[ast.stmt], int, str]  # a later part of a scope that may rebind a name
Scope = tuple[list[ast.stmt], tuple[str, ...], tuple[str, ...] | None, bool, tuple[Shadow, ...]]


def hidden_bindings(tree: ast.Module, name: str, collected: set[str]) -> Counter:
    """``(scope path, ast.dump)`` counts of each class- or function-body statement binding
    ``name`` where pytest does not collect it (#1127): a function body, a class whose chain
    pytest did not collect under ``name`` (``collected`` holds ``A::B::name`` ids of this file),
    or a binding a later statement of the same scope shadows (also for the class chain). The
    path names every enclosing class and function. A constant value or a bare annotation is never
    a test and does not count. Iterative, and the shadowing is checked only for a binding found,
    so a deep or long file costs one walk."""
    found: Counter = Counter()
    # body, path, chain (None in a function), counts, shadow checks
    scopes: list[Scope] = [(tree.body, (), (), False, ())]
    while scopes:
        body, path, chain, counts, shadows = scopes.pop()
        for i, stmt in enumerate(body):
            if counts and binds_in_scope(stmt, name) and not _never_a_test(stmt):
                key = None if chain is None else "::".join((*chain, name))
                checks = (*shadows, (body, i + 1, name))
                if key not in collected or any(_shadowed(b, j, n) for b, j, n in checks):
                    found[(path, ast.dump(stmt))] += 1
            for node in _scopes_in(stmt):
                sub_path = (*path, node.name)
                if isinstance(node, ast.ClassDef) and chain is not None:
                    sub = (*shadows, (body, i + 1, node.name))
                    scopes.append((node.body, sub_path, (*chain, node.name), True, sub))
                else:
                    scopes.append((node.body, sub_path, None, True, ()))
    return found


def _shadowed(body: list[ast.stmt], start: int, name: str) -> bool:
    return any(binds_in_scope(body[j], name) for j in range(start, len(body)))


def _scopes_in(stmt: ast.stmt):
    """``stmt`` itself when it is a class or function, else the classes and functions inside it
    through blocks (never through another scope)."""
    pending: list[ast.AST] = [stmt]
    while pending:
        node = pending.pop()
        if isinstance(node, (ast.ClassDef, *FUNCTIONS)):
            yield node
            continue
        pending.extend(ast.iter_child_nodes(node))


FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)


def _never_a_test(stmt: ast.stmt) -> bool:
    """A value built only from literals (``1``, ``1 + 2``, ``(1, 'a')``) is never collected."""
    if isinstance(stmt, ast.AnnAssign) and stmt.value is None:
        return True
    if not isinstance(stmt, (ast.Assign, ast.AnnAssign)) or stmt.value is None:
        return False
    return all(isinstance(n, LITERAL_PARTS) for n in ast.walk(stmt.value))


LITERAL_PARTS = (ast.Constant, ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare, ast.Tuple)
LITERAL_PARTS += (ast.List, ast.Set, ast.Dict, ast.expr_context, ast.operator, ast.unaryop)
LITERAL_PARTS += (ast.boolop, ast.cmpop)


SETTERS = {"setattr", "delattr", "update", "setdefault", "__setitem__", "__delitem__"}
SETTERS |= {"__setattr__", "__delattr__"}


def rebinds_by_string(tree: ast.Module, name: str) -> bool:
    """Whether the file can rebind ``name`` in a way the binding walk does not follow (#1127):
    ``globals()['name'] = f``, ``globals().update(name=f)``, ``setattr(mod, 'name', f)``,
    ``TestA.name = f``. The base then may collect other code under that name. At module or class
    level any such store counts; inside a function only one on a namespace (``globals()``,
    ``sys.modules``, ``__dict__``, a class of this file) does, so ``self.name = 1`` or
    ``config['name'] = 1`` in a fixture is not a rebinding."""
    classes = {n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
    for n, in_function in _scoped(tree):
        inside = classes if in_function else None  # None: module or class level
        store = isinstance(getattr(n, "ctx", None), (ast.Store, ast.Del))
        if store and isinstance(n, ast.Subscript) and _is_str(n.slice, name):
            if _counts(n.value, inside):
                return True
        if store and isinstance(n, ast.Attribute) and n.attr == name and _counts(n.value, inside):
            return True
        if isinstance(n, ast.Call) and _callee(n.func) in SETTERS:
            keys = [k for a in n.args if isinstance(a, ast.Dict) for k in a.keys]
            named = any(_is_str(a, name) for a in [*n.args, *keys])
            if named or any(k.arg == name for k in n.keywords):
                func = n.func
                target = func.value if isinstance(func, ast.Attribute) else None
                if _callee(func) in {"setattr", "delattr"} and n.args:
                    target = n.args[0]
                if target is None or _counts(target, inside):
                    return True
    return False


def _scoped(tree: ast.Module):
    """Every node with whether it sits inside a function body. Iterative: no recursion limit."""
    pending: list[tuple[ast.AST, bool]] = [(tree, False)]
    while pending:
        node, in_function = pending.pop()
        yield node, in_function
        inner = in_function or isinstance(node, (*FUNCTIONS, ast.Lambda))
        pending.extend((child, inner) for child in ast.iter_child_nodes(node))


def _counts(target: ast.expr, classes: set[str] | None) -> bool:
    """Outside a function (``classes`` None) every target counts; inside, only a namespace."""
    return classes is None or _namespace(target, classes)


def _namespace(node: ast.expr, classes: set[str]) -> bool:
    """A namespace whose names pytest may collect: ``globals()``/``locals()``/``vars()``,
    ``sys.modules[...]``, any ``__dict__``, or a class defined in this file."""
    if isinstance(node, ast.Call):
        return _callee(node.func) in {"globals", "locals", "vars", "__import__"}
    if isinstance(node, ast.Subscript):
        return _namespace(node.value, classes)
    if isinstance(node, ast.Attribute):
        return node.attr in {"__dict__", "modules"} or _namespace(node.value, classes)
    return isinstance(node, ast.Name) and node.id in classes


def _callee(func: ast.expr) -> str:
    if isinstance(func, ast.Name):
        return func.id
    return func.attr if isinstance(func, ast.Attribute) else ""


def _is_str(node: ast.AST | None, name: str) -> bool:
    return isinstance(node, ast.Constant) and node.value == name
