"""The acceptance obligations of a test file, counted statically: every test pytest would collect.

The coverage denominator (#303) is fixed before any candidate runs and includes what cannot be
protected, so this module counts the test ids pytest would collect (module functions, methods of
``Test*`` classes and ``unittest.TestCase`` subclasses, each parametrized case, the product of
stacked ``parametrize`` decorators and fixture ``params``) by reading the file, never importing it.
Where a count cannot be known statically (parametrize values that are not literals) the test still
counts once, refused, and ``exact`` is False so the caller can say the count is a lower bound.
Anything that makes a test depend on more than its own body (fixtures, autouse fixtures, setup
hooks, skip/xfail marks, other decorators) refuses the item here, with the reason.
"""

from __future__ import annotations

import ast
import itertools
from collections.abc import Iterator
from dataclasses import dataclass, field

from .resolve import dotted

SETUP_HOOKS = {
    "setup_module",
    "teardown_module",
    "setup_function",
    "teardown_function",
    "setUpModule",
    "tearDownModule",
}
CLASS_HOOKS = {
    "setup_method",
    "teardown_method",
    "setup_class",
    "teardown_class",
    "setUp",
    "tearDown",
    "setUpClass",
    "tearDownClass",
    "setup",
    "teardown",
}
REFUSING_MARKS = {"skip", "skipif", "xfail", "usefixtures"}


@dataclass
class Item:
    test_id: str
    line: int
    func: ast.FunctionDef | ast.AsyncFunctionDef
    bindings: dict[str, ast.expr] = field(default_factory=dict)
    refusal: str | None = None
    exact: bool = True


@dataclass
class Param:
    bindings: dict[str, ast.expr]
    refusal: str | None = None


@dataclass
class Fixtures:
    """Fixture definitions visible to the file: name -> number of params (None: not static)."""

    params: dict[str, int | None] = field(default_factory=dict)
    autouse: list[str] = field(default_factory=list)


def _mark(node: ast.expr) -> tuple[str, ast.Call | None] | None:
    """('parametrize', call) for ``pytest.mark.parametrize(...)`` / ``mark.parametrize``."""
    call = node if isinstance(node, ast.Call) else None
    parts = dotted(call.func if call else node)
    if parts and len(parts) >= 2 and parts[-2] == "mark":
        return parts[-1], call
    return None


def _fixture_decorator(node: ast.expr) -> ast.Call | bool:
    target = node.func if isinstance(node, ast.Call) else node
    parts = dotted(target)
    if parts and parts[-1] == "fixture":
        return node if isinstance(node, ast.Call) else True
    return False


def _fixture_spec(func: ast.FunctionDef | ast.AsyncFunctionDef) -> dict[str, ast.expr] | None:
    """The keywords of func's fixture decorator ({} for a bare one), None if not a fixture."""
    for deco in func.decorator_list:
        spec = _fixture_decorator(deco)
        if isinstance(spec, ast.Call):
            return {k.arg: k.value for k in spec.keywords if k.arg}
        if spec:
            return {}
    return None


def is_autouse(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    auto = (_fixture_spec(func) or {}).get("autouse")
    return isinstance(auto, ast.Constant) and auto.value is True


def fixtures_of(trees: list[ast.Module]) -> Fixtures:
    """Fixtures defined in the given trees: params of all of them; autouse at module level only
    (a class's autouse fixtures are found with the class)."""
    found = Fixtures()
    for tree in trees:
        for node in tree.body:
            funcs = node.body if isinstance(node, ast.ClassDef) else [node]
            for func in funcs:
                if not isinstance(func, ast.FunctionDef | ast.AsyncFunctionDef):
                    continue
                spec = _fixture_spec(func)
                if spec is None:
                    continue
                if "params" in spec:
                    p = spec["params"]
                    found.params[func.name] = (
                        len(p.elts) if isinstance(p, ast.List | ast.Tuple) else None
                    )  # noqa: E501
                if func is node and is_autouse(func):
                    found.autouse.append(func.name)
    return found


def _module_lists(tree: ast.Module) -> dict[str, ast.expr]:
    out: dict[str, ast.expr] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name):
                out[target.id] = node.value
    return out


def parametrize(call: ast.Call, consts: dict[str, ast.expr]) -> list[Param] | None:
    """The cases of one parametrize mark, or None when their number cannot be known."""
    if len(call.args) < 2:
        return None
    names_node, values = call.args[0], call.args[1]
    if isinstance(names_node, ast.Constant) and isinstance(names_node.value, str):
        names = [n.strip() for n in names_node.value.split(",") if n.strip()]
    elif isinstance(names_node, ast.List | ast.Tuple) and all(
        isinstance(e, ast.Constant) and isinstance(e.value, str) for e in names_node.elts
    ):
        names = [e.value for e in names_node.elts]  # type: ignore[attr-defined]
    else:
        return None
    if isinstance(values, ast.Name):
        values = consts.get(values.id, values)
    if not isinstance(values, ast.List | ast.Tuple):
        return None
    indirect = any(k.arg == "indirect" for k in call.keywords)
    if not values.elts:
        return [Param({}, "empty parametrize: collected as one skipped test")]
    return [_param(names, elt, indirect) for elt in values.elts]


def _param(names: list[str], elt: ast.expr, indirect: bool) -> Param:
    refusal = "indirect parametrization (values go through fixtures)" if indirect else None
    if isinstance(elt, ast.Call) and (dotted(elt.func) or [""])[-1] == "param":
        if any(k.arg == "marks" for k in elt.keywords):
            refusal = refusal or "this case carries marks (skip/xfail/...)"
        items: list[ast.expr] = list(elt.args)
    elif len(names) == 1:
        items = [elt]
    elif isinstance(elt, ast.List | ast.Tuple):
        items = list(elt.elts)
    else:
        return Param({}, "the case's values are not a literal tuple")
    if len(items) != len(names) or any(isinstance(i, ast.Starred) for i in items):
        return Param({}, "the case's values do not match the parameter names statically")
    return Param(dict(zip(names, items, strict=True)), refusal)


def _marks_of(decorators: list[ast.expr], extra: list[ast.expr]) -> tuple[list, str | None]:
    """(parametrize calls, refusal) for a decorator list plus pytestmark entries."""
    calls: list[ast.Call] = []
    refusal = None
    for deco in [*decorators, *extra]:
        mark = _mark(deco)
        if mark is None:
            name = ".".join(dotted(deco.func if isinstance(deco, ast.Call) else deco) or ["?"])
            refusal = refusal or f"decorated with {name} (not a plain test)"
        elif mark[0] == "parametrize" and mark[1] is not None:
            calls.append(mark[1])
        elif mark[0] in REFUSING_MARKS:
            refusal = refusal or f"marked {mark[0]}"
    return calls, refusal


def _pytestmark(body: list[ast.stmt]) -> list[ast.expr]:
    for node in body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "pytestmark" for t in node.targets
        ):
            value = node.value
            return list(value.elts) if isinstance(value, ast.List | ast.Tuple) else [value]
    return []


def collect(tree: ast.Module, path: str, conftests: list[ast.Module]) -> list[Item]:
    """Every test id pytest would collect from tree (path is how ids are spelt)."""
    fixtures = fixtures_of([*conftests, tree])
    consts = _module_lists(tree)
    module_marks = _pytestmark(tree.body)
    names = {n.name for n in tree.body if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)}
    base = None
    if fixtures.autouse:
        base = f"autouse fixture {fixtures.autouse[0]} is in effect"
    elif names & SETUP_HOOKS:
        base = f"module setup hook {sorted(names & SETUP_HOOKS)[0]}"
    items: list[Item] = []
    classes = {n.name: n for n in tree.body if isinstance(n, ast.ClassDef)}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.startswith(
            "test"
        ):
            items += _expand(node, f"{path}::{node.name}", [], module_marks, consts, fixtures, base)
        elif isinstance(node, ast.ClassDef) and _is_test_class(node):
            items += _class_items(node, classes, path, module_marks, consts, fixtures, base)
    return items


def _is_test_class(node: ast.ClassDef) -> bool:
    if any((dotted(b) or [""])[-1] == "TestCase" for b in node.bases):
        return True
    has_init = any(isinstance(n, ast.FunctionDef) and n.name == "__init__" for n in node.body)
    return node.name.startswith("Test") and not has_init


def _methods(node: ast.ClassDef, classes: dict[str, ast.ClassDef], depth: int = 0) -> dict:
    found: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
    if depth < 10:
        for b in node.bases:
            if isinstance(b, ast.Name) and b.id in classes and b.id != node.name:
                found.update(_methods(classes[b.id], classes, depth + 1))
    for n in node.body:
        if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef):
            found[n.name] = n
    return found


def _class_items(node, classes, path, module_marks, consts, fixtures, base) -> list[Item]:
    methods = _methods(node, classes)
    hooks = sorted(set(methods) & CLASS_HOOKS)
    autouse = [m.name for m in methods.values() if is_autouse(m)]
    refusal = base or (f"class setup hook {hooks[0]}" if hooks else None)
    refusal = refusal or (f"class autouse fixture {autouse[0]}" if autouse else None)
    class_marks = [*module_marks, *node.decorator_list, *_pytestmark(node.body)]
    items: list[Item] = []
    for name, func in methods.items():
        if name.startswith("test"):
            test_id = f"{path}::{node.name}::{name}"
            items += _expand(func, test_id, class_marks, [], consts, fixtures, refusal, method=True)
    return items


def _expand(func, test_id, outer, module_marks, consts, fixtures, refusal, *, method=False):
    calls, mark_refusal = _marks_of(func.decorator_list, [*outer, *module_marks])
    refusal = refusal or mark_refusal
    if isinstance(func, ast.AsyncFunctionDef):
        refusal = refusal or "async test"
    tables: list[list[Param]] = []
    for call in calls:
        table = parametrize(call, consts)
        if table is None:
            return [
                Item(test_id, func.lineno, func, {}, "parametrize values are not static", False)
            ]
        tables.append(table)
    bound = {name for table in tables for p in table for name in p.bindings}
    args = [a.arg for a in [*func.args.posonlyargs, *func.args.args, *func.args.kwonlyargs]]
    if method and args:
        args = args[1:]
    used = [a for a in args if a not in bound]
    count, exact = 1, True
    for name in used:
        n = fixtures.params.get(name, 1)
        exact = exact and n is not None
        count *= n or 1
    if used:
        refusal = refusal or f"uses fixture(s): {', '.join(used)}"
    return list(_items(func, test_id, tables, count, refusal, exact))


def _items(func, test_id, tables, count, refusal, exact) -> Iterator[Item]:
    combos = list(itertools.product(*tables)) if tables else [()]
    many = len(combos) * count > 1
    index = 0
    for combo in combos:
        bindings: dict[str, ast.expr] = {}
        case_refusal = refusal
        for param in combo:
            bindings.update(param.bindings)
            case_refusal = case_refusal or param.refusal
        for _ in range(count):
            suffix = f"[{index}]" if many else ""
            yield Item(test_id + suffix, func.lineno, func, bindings, case_refusal, exact)
            index += 1
