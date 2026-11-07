"""#982 (#998): the names a base module's fixtures may register, however they are written, for
conftest_names. A fixture(...) call, a two-step fixture(...)(fn), an assignment holding one, an
alias of pytest.fixture (also one another read module binds and this one imports under any name,
``exported``), and a ``name=`` that cannot be read (refused, fail closed)."""

from __future__ import annotations

import ast
from collections.abc import Iterator

from cairnwork.deferred_overlay import TestCommitRejected

CODE = "TEST_COMMIT_CONFTEST_STATIC"


def exported(trees) -> frozenset[str]:
    """Names any of ``trees`` binds to ``fixture`` or an alias of it, through re-exports under any
    name (#998 r1: ``from helper import fx`` where helper binds ``fx = pytest.fixture``). Only an
    exact reference counts here (``_ref``; #998 r2, Claude): a name bound to an expression that
    merely mentions the word, such as ``run = load_fixture()``, stays an alias of its own module
    only, so it never makes an unrelated import of ``run`` elsewhere an alias."""
    names: frozenset[str] = frozenset()
    while True:
        more = names.union(*(_aliases(tree, names, exact=True) for tree in trees))
        if more == names:
            return names
        names = more


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


def registered(tree: ast.Module, extra: frozenset[str] = frozenset()) -> set[str]:
    """#982: names a fixture in ``tree`` may register, however it is written: every literal
    ``name=`` of any call; for a call of ``fixture`` or an alias of it (``_aliases``; also
    ``fixture(...)(fn)``), the functions passed to it and the targets of the assignment holding it.
    A ``name=`` of such a call that is not a literal counts every string constant of ``tree`` when
    it is a name the module binds to string constants only; any other one is refused."""
    aliases, names = _aliases(tree, extra), set()
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
                names |= _strings(tree, k.value)
    return names


def _aliases(
    tree: ast.Module, extra: frozenset[str] = frozenset(), exact: bool = False
) -> set[str]:
    """Names bound to ``fixture`` another way: ``from pytest import fixture as fx``,
    ``f = pytest.fixture``, a name ``extra`` holds imported under any name or used as a module
    attribute (``exported``), and names bound to those (a name, an annotated name, a module
    attribute, an item of a tuple target or an assignment expression). ``exact``: the word must be
    the whole name (``_fixture``)."""
    names = {
        a.asname or a.name
        for n in ast.walk(tree)
        if isinstance(n, ast.Import | ast.ImportFrom)
        for a in n.names
        if _word(a.name.rsplit(".", 1)[-1] if exact else a.name, exact)
        or a.name.rsplit(".", 1)[-1] in extra
    } | (
        set(extra)
        if any(isinstance(n, ast.ImportFrom) and n.names[0].name == "*" for n in ast.walk(tree))
        else set()
    )  # a star import binds them all
    names |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute) and n.attr in extra}
    while True:
        more = {  # #998 r2: m.fx = pytest.fixture exports fx; so do fx: T = ..., a, fx = ...
            t.id if isinstance(t, ast.Name) else t.attr for n in ast.walk(tree)
            if isinstance(n, ast.Assign | ast.AnnAssign | ast.NamedExpr) and n.value
            and (_ref(n.value, names) if exact else _fixture(n.value, names))
            for top in (n.targets if isinstance(n, ast.Assign) else [n.target])
            for t in _items(top) if isinstance(t, ast.Name | ast.Attribute)
        } - names  # fmt: skip
        if not more:
            return names
        names |= more


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


def _word(word: str, exact: bool) -> bool:
    return word == "fixture" if exact else "fixture" in word


def _ref(node: ast.expr, aliases: set[str]) -> bool:
    """For ``exported``, only a value that is ``fixture`` or an alias of it itself: the name or
    attribute, or ``getattr(m, "fixture")``. Not a call of one (its result is a decorator or a
    fixture, read where it is made), and not any expression mentioning the word: across modules
    either would spread to common names (``body = self.read()`` once ``read`` is an alias)."""
    if isinstance(node, ast.Name | ast.Attribute):
        word = node.id if isinstance(node, ast.Name) else node.attr
        return word == "fixture" or word in aliases
    # a, fx = 1, fixture; fx = fixture if c else f
    if isinstance(node, ast.Tuple | ast.List | ast.IfExp):
        items = node.elts if isinstance(node, ast.Tuple | ast.List) else [node.body, node.orelse]
        return any(_ref(item, aliases) for item in items)
    return (
        isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id == "getattr" and len(node.args) > 1
        and isinstance(node.args[1], ast.Constant) and node.args[1].value == "fixture"
    )  # fmt: skip


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
    return False


def _strings(tree: ast.Module, value: ast.expr) -> set[str]:
    """A fixture's ``name=`` that is not a literal: every string constant of ``tree`` when it is a
    name the module binds, and binds only by assigning a string constant (no star import); refused
    otherwise (fail closed: the registered name cannot be read)."""
    name = value.id if isinstance(value, ast.Name) else ""
    nodes = list(ast.walk(tree))
    strings = {n.value for n in nodes if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    good = {
        id(n.targets[0]) for n in nodes if isinstance(n, ast.Assign) and len(n.targets) == 1
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
