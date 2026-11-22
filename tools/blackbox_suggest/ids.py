"""Whether pytest's parametrize ids in a module are certainly distinct (#370).

With ``strict_parametrization_ids`` on (or ``strict``, which turns it on), pytest stops collection
when two cases of one parametrize call, or of one fixture's ``params``, get the same id: no test
runs. This reader works an id out only where pytest builds it from a literal: a string id given
with ``pytest.param(id=...)`` or in ``ids=[...]``, or a string, number, bool or None value. Any
other case, an ``ids`` callable, a ``pytest_make_parametrize_id`` hook or a ``parametrize`` that
is not called where it is named leaves the ids unknown.
"""

from __future__ import annotations

import ast

from .resolve import dotted, module_env


def distinct_ids(trees: list[ast.Module]) -> bool:
    """Whether every parametrize call and fixture ``params`` in trees has ids known to differ."""
    for tree in trees:
        consts, called = module_env(tree), set()
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                if node.name == "pytest_make_parametrize_id":
                    return False
            elif isinstance(node, ast.Call):
                given = {k.arg: k.value for k in node.keywords}
                if (dotted(node.func) or [""])[-1] == "parametrize":
                    called.add(id(node.func))
                    given |= dict(
                        zip(("argnames", "argvalues", "indirect", "ids"), node.args, strict=False)
                    )
                    names = _names(given.get("argnames"))
                    if not _unique(names, given.get("argvalues"), given.get("ids"), consts):
                        return False
                elif "params" in given and not _unique(
                    ["p"], given["params"], given.get("ids"), consts
                ):
                    return False
        for node in ast.walk(tree):
            named = isinstance(node, ast.Attribute) and node.attr == "parametrize"
            named = named or isinstance(node, ast.Name) and node.id == "parametrize"
            if named and id(node) not in called:
                return False
    return True


def _names(node: ast.expr | None) -> list[str] | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [n.strip() for n in node.value.split(",") if n.strip()]
    if isinstance(node, ast.List | ast.Tuple):  # one name in a list: each value is a tuple
        names = [str(e.value) for e in node.elts if isinstance(e, ast.Constant)]
        texts = all(isinstance(e, ast.Constant) and isinstance(e.value, str) for e in node.elts)
        return names if texts and len(node.elts) > 1 else None
    return None


def _unique(names, values, ids, consts: dict[str, ast.expr]) -> bool:
    values = consts.get(values.id, values) if isinstance(values, ast.Name) else values
    ids = consts.get(ids.id, ids) if isinstance(ids, ast.Name) else ids
    if names is None or not isinstance(values, ast.List | ast.Tuple):
        return False
    if ids is not None and not isinstance(ids, ast.List | ast.Tuple):
        return False
    given = list(ids.elts) if ids is not None else []
    seen: set[str] = set()
    for index, case in enumerate(values.elts):
        token = _case_id(names, case, given[index] if index < len(given) else None)
        if token is None or token in seen:
            return False
        seen.add(token)
    return True


def _case_id(names: list[str], case: ast.expr, given: ast.expr | None) -> str | None:
    """The id pytest gives one case: its own id, then the ``ids`` entry, then its values."""
    items = [case]
    if isinstance(case, ast.Call) and (dotted(case.func) or [""])[-1] == "param":
        own = {k.arg: k.value for k in case.keywords}.get("id")
        if own is not None and not _is_none(own):
            text = own.value if isinstance(own, ast.Constant) else None
            return _token(own) if isinstance(text, str) else None
        items = list(case.args)
    elif len(names) > 1:
        if not isinstance(case, ast.List | ast.Tuple):
            return None
        items = list(case.elts)
    if given is not None and not _is_none(given):
        return _token(given)
    tokens = [_token(item) for item in items]
    if len(tokens) != len(names) or None in tokens:
        return None
    return "-".join(tokens)  # type: ignore[arg-type]


def _is_none(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and node.value is None


def _token(node: ast.expr) -> str | None:
    """pytest's id for a literal value: numbers, bools and None as str, a string as itself
    (pytest escapes it to ASCII, which keeps distinct strings distinct)."""
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        inner = node.operand.value if isinstance(node.operand, ast.Constant) else None
        if isinstance(inner, int | float) and not isinstance(inner, bool):
            return str(-inner)
        return None
    if not isinstance(node, ast.Constant):
        return None
    value = node.value
    if isinstance(value, str):
        return value
    if value is None or isinstance(value, int | float | complex):
        return str(value)
    return None
