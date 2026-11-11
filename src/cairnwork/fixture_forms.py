"""#982 (#998): the names a base module's fixtures may register, however they are written, for
conftest_names. A fixture(...) call, a two-step fixture(...)(fn), an assignment holding one, an
alias of pytest.fixture the module binds, and a ``name=`` that cannot be read (refused, fail
closed)."""

from __future__ import annotations

import ast
from collections.abc import Iterator
from posixpath import dirname

from cairnwork.deferred_overlay import TestCommitRejected

CODE = "TEST_COMMIT_CONFTEST_STATIC"


def module_level(body: list[ast.stmt]) -> Iterator[ast.stmt]:
    """#982: each statement of ``body`` and of the blocks inside it that run at module level."""
    for node in body:
        yield node
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            continue
        blocks = [getattr(node, field, []) for field in ("body", "orelse", "finalbody")]
        blocks += [
            part.body for part in (*getattr(node, "handlers", []), *getattr(node, "cases", []))
        ]
        for block in blocks:
            yield from module_level(block)


def registered(tree: ast.Module, extra: set[str] | None = None) -> set[str]:
    """#982: names a fixture in ``tree`` may register, however it is written: every literal
    ``name=`` of any call; for a call of ``fixture`` or an alias of it (``_aliases``; also
    ``fixture(...)(fn)``), the functions passed to it and the targets of the assignment holding it.
    A ``name=`` of such a call that is not a literal counts every string constant of ``tree`` when
    it is a name the module binds to string constants only. Any other one is refused for a call of
    ``fixture`` itself (``_exact``). Not refused, as on main: a call that only mentions the word,
    and, in a module that defines ``fixture`` (pytest's own source), a call forwarding a parameter
    of the function around it; the name is set where that wrapper is called (#998 r3, Claude;
    #1019 r1, Codex: not the whole module)."""
    aliases, names = _aliases(tree, extra=extra), set()
    exact, forwarded = _exact_aliases(tree), _forwarded(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign | ast.AnnAssign) and node.value is not None:
            if any(_fixture_call(c, aliases) for c in ast.walk(node.value)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                names |= {n.id for t in targets for n in ast.walk(t) if isinstance(n, ast.Name)}
        if not isinstance(node, ast.Call):
            continue
        fixture = _fixture_call(node, aliases)
        if fixture:
            names |= {a.id for a in node.args if isinstance(a, ast.Name)}
        for k in node.keywords:
            if k.arg == "name" and isinstance(k.value, ast.Constant):
                names |= {k.value.value} if isinstance(k.value.value, str) else set()
            elif k.arg in ("name", None) and fixture:
                try:
                    names |= _strings(tree, k.value)
                except TestCommitRejected:
                    if _exact(node.func, exact) and _name(k.value) not in forwarded.get(
                        id(node), ()
                    ):
                        raise
    return names


def _forwarded(tree: ast.Module) -> dict[int, set[str]]:
    """In a module that defines ``fixture``: each call inside a function, with the parameters of
    the functions around it (a wrapper forwarding its own ``name``)."""
    if not any(getattr(n, "name", None) == "fixture" for n in module_level(tree.body)):
        return {}
    out: dict[int, set[str]] = {}
    for d in ast.walk(tree):
        if isinstance(d, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
            a = d.args
            params = {
                x.arg for x in (*a.posonlyargs, *a.args, *a.kwonlyargs, a.vararg, a.kwarg) if x
            }
            for c in ast.walk(d):
                if isinstance(c, ast.Call):
                    out.setdefault(id(c), set()).update(params)
    return out


def _name(node: ast.expr) -> str:
    return node.id if isinstance(node, ast.Name) else ""


def _exact_aliases(tree: ast.Module) -> set[str]:
    """Names ``tree`` binds to ``fixture`` itself: ``from pytest import fixture as fx``, then
    ``f = fx``, ``f: T = pytest.fixture``, ``(f := ...)``, every target of ``a = f = ...`` and the
    matching item of ``f, b = fx, 1`` (#1019 r1, Claude C2/C3), chained (no recursion)."""
    names = {
        a.asname or a.name
        for n in ast.walk(tree)
        if isinstance(n, ast.ImportFrom)
        for a in n.names
        if a.name == "fixture"
    }
    todo = [
        (t, n.value)
        for n in ast.walk(tree)
        if isinstance(n, ast.Assign | ast.AnnAssign | ast.NamedExpr) and n.value is not None
        for t in (n.targets if isinstance(n, ast.Assign) else [n.target])
    ]
    pairs = []
    while todo:  # pair a tuple target with a tuple value item by item
        t, v = todo.pop()
        if isinstance(t, ast.Tuple | ast.List) and isinstance(v, ast.Tuple | ast.List):
            if len(t.elts) == len(v.elts):
                todo += zip(t.elts, v.elts, strict=True)
        else:
            pairs.append((t, v))
    while True:
        more = {t.id for t, v in pairs if isinstance(t, ast.Name) and _exact(v, names)} - names
        if not more:
            return names
        names |= more


def _exact(func: ast.expr, exact: set[str]) -> bool:
    """``func`` is ``fixture`` itself: ``x.fixture``, ``fixture`` or a name ``_exact_aliases``
    holds, ``getattr(x, "fixture")``, or one of those in an assignment expression."""
    if isinstance(func, ast.NamedExpr):
        func = func.value
    if isinstance(func, ast.Call):
        args = func.args
        return (
            isinstance(func.func, ast.Name) and func.func.id == "getattr" and len(args) > 1
            and isinstance(args[1], ast.Constant) and args[1].value == "fixture"
        )  # fmt: skip
    if isinstance(func, ast.Attribute):
        return func.attr == "fixture"
    return isinstance(func, ast.Name) and (func.id == "fixture" or func.id in exact)


def _aliases(tree: ast.Module, extra: set[str] | None = None) -> set[str]:
    """Names ``tree`` binds to ``fixture`` another way: ``from pytest import fixture as fx``,
    ``f = pytest.fixture``, and names bound to those (a name, an annotated name, a module
    attribute, an item of a tuple target or an assignment expression), plus resolved source
    module aliases."""
    names = set(extra or ()) | {
        a.asname or a.name
        for n in ast.walk(tree)
        if isinstance(n, ast.Import | ast.ImportFrom)
        for a in n.names
        if "fixture" in a.name
    }
    while True:
        more = {  # #998 r2: m.fx = pytest.fixture binds fx; so do fx: T = ..., a, fx = ...
            t.id if isinstance(t, ast.Name) else t.attr for n in ast.walk(tree)
            if isinstance(n, ast.Assign | ast.AnnAssign | ast.NamedExpr) and n.value
            and _fixture(n.value, names)
            for top in (n.targets if isinstance(n, ast.Assign) else [n.target])
            for t in _items(top) if isinstance(t, ast.Name | ast.Attribute)
        } - names  # fmt: skip
        if not more:
            return names
        names |= more


def _import_head(file: str, module: str, level: int) -> str:
    if level == 0:
        return module
    up = dirname(file)
    for _ in range(level - 1):
        up = dirname(up)
    return ".".join(filter(None, [*(up.split("/") if level else ()), module]))


def _dotted(node: ast.expr) -> str:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return ""


def _is_value_fixture(
    value: ast.expr,
    aliases: set[str],
    mod_map: dict[str, set[str]],
    all_aliases: dict[str, set[str]],
) -> bool:
    if isinstance(value, ast.NamedExpr):
        value = value.value
    if _exact(value, aliases):
        return True
    if isinstance(value, ast.Attribute) and isinstance(value.value, ast.Name):
        dot = f"{value.value.id}.{value.attr}"
        if dot in aliases:
            return True
        for target in mod_map.get(value.value.id, ()):
            if value.attr in all_aliases.get(target, ()):
                return True
    return False


def _source_alias_inputs(
    all_trees: dict[str, ast.Module], index: dict[str, set[str]]
) -> tuple[
    dict[str, dict[str, set[str]]],
    dict[str, list[tuple[str, str, str]]],
    dict[str, list[tuple[str, ast.expr]]],
    dict[str, set[str]],
]:
    """Collect imports and module-level assignments without resolving their aliases yet."""
    mod_maps: dict[str, dict[str, set[str]]] = {}
    from_edges: dict[str, list[tuple[str, str, str]]] = {}
    file_pairs: dict[str, list[tuple[str, ast.expr]]] = {}
    all_aliases: dict[str, set[str]] = {}
    for path, tree in all_trees.items():
        stmts = list(module_level(tree.body))
        aliases: set[str] = set()
        mmap: dict[str, set[str]] = {}
        edges: list[tuple[str, str, str]] = []
        _collect_source_imports(path, stmts, all_trees, index, aliases, mmap, edges)
        mod_maps[path] = mmap
        from_edges[path] = edges
        file_pairs[path] = _source_assignment_pairs(stmts)
        all_aliases[path] = aliases
    return mod_maps, from_edges, file_pairs, all_aliases


def _collect_source_imports(
    path: str,
    stmts: list[ast.stmt],
    all_trees: dict[str, ast.Module],
    index: dict[str, set[str]],
    aliases: set[str],
    mod_map: dict[str, set[str]],
    edges: list[tuple[str, str, str]],
) -> None:
    """Record fixture imports and imported module paths for one source file."""
    for node in stmts:
        if isinstance(node, ast.ImportFrom):
            if node.module == "pytest" and node.level == 0:
                aliases.update(a.asname or a.name for a in node.names if a.name == "fixture")
                continue
            head = _import_head(path, node.module or "", node.level)
            targets = (index.get(head) or set()) & all_trees.keys() - {path}
            edges.extend(
                (target, a.name, a.asname or a.name) for target in targets for a in node.names
            )
            for a in node.names:
                sub = f"{head}.{a.name}".strip(".")
                sub_targets = (index.get(sub) or set()) & all_trees.keys() - {path}
                if sub_targets:
                    mod_map.setdefault(a.asname or a.name, set()).update(sub_targets)
        elif isinstance(node, ast.Import):
            for a in node.names:
                local = a.asname or a.name.split(".")[0]
                targets = (index.get(a.name) or set()) & all_trees.keys() - {path}
                if targets:
                    mod_map.setdefault(local, set()).update(targets)


def _source_assignment_pairs(stmts: list[ast.stmt]) -> list[tuple[str, ast.expr]]:
    """Flatten module-level assignments to name/value pairs, including tuple unpacking."""
    todo = [
        (target, node.value)
        for node in stmts
        if isinstance(node, ast.Assign | ast.AnnAssign | ast.NamedExpr) and node.value is not None
        for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
    ]
    pairs: list[tuple[str, ast.expr]] = []
    while todo:
        target, value = todo.pop(0)
        if isinstance(target, ast.Tuple | ast.List) and isinstance(value, ast.Tuple | ast.List):
            if len(target.elts) == len(value.elts):
                todo.extend(zip(target.elts, value.elts, strict=True))
        elif isinstance(target, ast.Name):
            pairs.append((target.id, value))
    return pairs


def _propagate_source_aliases(
    all_trees: dict[str, ast.Module],
    mod_maps: dict[str, dict[str, set[str]]],
    from_edges: dict[str, list[tuple[str, str, str]]],
    file_pairs: dict[str, list[tuple[str, ast.expr]]],
    all_aliases: dict[str, set[str]],
) -> None:
    """Grow finite alias sets until stable; every round adds values from finite source names."""
    changed = True
    while changed:
        changed = False
        for path in all_trees:
            if _propagate_file_aliases(path, mod_maps, from_edges, file_pairs, all_aliases):
                changed = True


def _propagate_file_aliases(
    path: str,
    mod_maps: dict[str, dict[str, set[str]]],
    from_edges: dict[str, list[tuple[str, str, str]]],
    file_pairs: dict[str, list[tuple[str, ast.expr]]],
    all_aliases: dict[str, set[str]],
) -> bool:
    """Propagate each known alias through the imports and assignments in one file."""
    aliases = all_aliases[path]
    before = len(aliases)
    for target, source, dest in from_edges[path]:
        target_aliases = all_aliases[target]
        if source == "*":
            aliases.update(name for name in target_aliases if "." not in name)
        elif source in target_aliases:
            aliases.add(dest)
    for local, targets in mod_maps[path].items():
        for target in targets:
            aliases.update(f"{local}.{name}" for name in all_aliases[target] if "." not in name)
    for name, value in file_pairs[path]:
        if name not in aliases and _is_value_fixture(value, aliases, mod_maps[path], all_aliases):
            aliases.add(name)
    return len(aliases) > before


def resolve_source_aliases(
    all_trees: dict[str, ast.Module],
    index: dict[str, set[str]],
) -> dict[str, set[str]]:
    """#1020: propagate fixture aliases across the modules conftest_names already reads."""
    mod_maps, from_edges, file_pairs, all_aliases = _source_alias_inputs(all_trees, index)
    _propagate_source_aliases(all_trees, mod_maps, from_edges, file_pairs, all_aliases)
    return all_aliases


def _fixture_call(node: ast.AST, aliases: set[str]) -> bool:
    """A call of ``fixture`` or an alias of it, or of what such a call returns (``_fixture`` reads
    the whole callee, so ``fixture(...)(fn)`` counts)."""
    return isinstance(node, ast.Call) and _fixture(node.func, aliases)


def _items(target: ast.expr) -> Iterator[ast.expr]:
    """The targets an assignment target binds: itself, or each item of a tuple or list."""
    if isinstance(target, ast.Tuple | ast.List):
        for item in target.elts:
            yield from _items(item)
    elif isinstance(target, ast.Starred):
        yield from _items(target.value)
    else:
        yield target


def _fixture(node: ast.expr, aliases: set[str]) -> bool:
    """``node`` mentions ``fixture`` (a name or attribute containing it, the string itself, as in
    ``getattr(pytest, "fixture")``) or an alias of it."""
    for n in ast.walk(node):
        if isinstance(n, ast.Constant):
            if n.value == "fixture":
                return True
            continue
        word = getattr(n, "id", getattr(n, "attr", ""))
        if isinstance(word, str) and ("fixture" in word or word in aliases):
            return True
        if isinstance(n, ast.Attribute):
            dot = _dotted(n)
            if dot and dot in aliases:
                return True
    return False


def _strings(tree: ast.Module, value: ast.expr) -> set[str]:
    """A fixture's ``name=`` that is not a literal: every string constant of ``tree`` when it is a
    name the module binds, and binds only by assigning a string constant (no star import); refused
    otherwise (fail closed: the registered name cannot be read)."""
    name = value.id if isinstance(value, ast.Name) else ""
    nodes = list(ast.walk(tree))
    strings = {n.value for n in nodes if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    good = {  # N = "a", or N: str = "a" (#1019 r1, Codex)
        id(n.targets[0] if isinstance(n, ast.Assign) else n.target) for n in nodes
        if (isinstance(n, ast.AnnAssign) or isinstance(n, ast.Assign) and len(n.targets) == 1)
        and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str)
    }  # fmt: skip
    if name and good.intersection(id(n) for n in nodes if getattr(n, "id", "") == name):
        if not any(_binds(n, name) and id(n) not in good for n in nodes):
            return strings
    raise TestCommitRejected(CODE, "a base fixture name= that is not a string literal")


def _binds(node: ast.AST, name: str) -> bool:
    """``node`` binds ``name``, or is a star import (it may)."""
    if isinstance(node, ast.Name):
        return node.id == name and not isinstance(node.ctx, ast.Load)
    if isinstance(node, ast.alias):
        return node.name == "*" or (node.asname or node.name.split(".")[0]) == name
    words = (getattr(node, "name", None), getattr(node, "rest", None))
    return name in words or (isinstance(node, ast.arg) and node.arg == name)
