"""The acceptance obligations of a test file, counted statically: every test pytest would collect.

The coverage denominator (#303) is fixed before any candidate runs and includes what cannot be
protected, so this module counts the test ids pytest would collect (module functions, methods of
``Test*`` classes and ``unittest.TestCase`` subclasses, each parametrized case, the product of
stacked ``parametrize`` decorators and fixture ``params``) by reading the file, never importing it.
Where a count cannot be known statically (parametrize values that are not literals, collection
hooks, tests defined under ``if``, classes with bases from elsewhere) the test still counts once,
refused, and ``exact`` is False so the caller can say the count is a lower bound. Anything that
makes a test depend on more than its own body refuses the item here, with the reason: fixtures
(autouse ones and their params included), setup hooks, any mark but ``parametrize``, other
decorators, pytest hooks or plugins, and module-level code that runs when pytest imports the test
module or a conftest.
"""

from __future__ import annotations

import ast
import itertools
from collections.abc import Iterator
from dataclasses import dataclass, field, replace

from .classes import class_marks, is_testcase, methods_of, pytestmark_of, unknown_bases
from .resolve import dotted, module_env

SETUP_HOOKS = {  # setup/teardown: nose style, which older pytest runs around every test
    "setup", "teardown", "setup_module", "teardown_module", "setup_function",
    "teardown_function", "setUpModule", "tearDownModule",
}  # fmt: skip
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


BUILTIN_FIXTURES = {
    "request", "tmp_path", "tmp_path_factory", "tmpdir", "tmpdir_factory", "monkeypatch",
    "capsys", "capfd", "capsysbinary", "capfdbinary", "caplog", "recwarn", "pytestconfig",
    "cache", "record_property", "record_xml_attribute", "record_testsuite_property",
    "doctest_namespace", "pytester", "testdir", "subtests",
}  # fmt: skip


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
    deps: dict[str, list[str]] = field(default_factory=dict)
    autouse: list[str] = field(default_factory=list)

    def count(self, names: list[str], bound: set[str]) -> tuple[int, bool]:
        """Cases these fixtures add, through the fixtures they request in turn. A name the
        test's parametrize binds is not a fixture there; a name defined nowhere visible (a
        plugin's, say) may have params, so the count is then not known."""
        seen: set[str] = set(bound)
        todo = list(names)
        count, exact = 1, True
        while todo:
            name = todo.pop()
            if name in seen:
                continue
            seen.add(name)
            if name not in self.params:
                exact = exact and name in BUILTIN_FIXTURES
                continue
            n = self.params[name]
            exact = exact and n is not None
            count *= n or 1
            todo += self.deps.get(name, [])
        return count, exact


def _mark(node: ast.expr) -> tuple[str, ast.Call | None] | None:
    """('parametrize', call) for ``pytest.mark.parametrize(...)`` / ``mark.parametrize``."""
    call = node if isinstance(node, ast.Call) else None
    parts = dotted(call.func if call else node)
    if parts and len(parts) >= 2 and parts[-2] == "mark":
        return parts[-1], call
    return None


def _fixture_decorator(node: ast.expr) -> ast.Call | bool:
    target = node.func if isinstance(node, ast.Call) else node
    if dotted(target) in (["fixture"], ["pytest", "fixture"]):  # not pytest.mark.fixture
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
    """Autouse unless the fixture gives no autouse or the literal False (``**`` options count)."""
    for deco in func.decorator_list:
        spec = _fixture_decorator(deco)
        if isinstance(spec, ast.Call) and any(k.arg is None for k in spec.keywords):
            return True
    auto = (_fixture_spec(func) or {}).get("autouse")
    return auto is not None and not (isinstance(auto, ast.Constant) and auto.value is False)


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
                p = spec.get("params")
                if p is None:
                    found.params[func.name] = 1  # a later definition overrides an earlier one
                else:
                    found.params[func.name] = (
                        len(p.elts) if isinstance(p, ast.List | ast.Tuple) else None
                    )
                params = [*func.args.posonlyargs, *func.args.args, *func.args.kwonlyargs]
                found.deps[func.name] = [a.arg for a in params if a.arg != "self"]
                if func is node and is_autouse(func):
                    found.autouse.append(func.name)
    return found


def parametrize(call: ast.Call, consts: dict[str, ast.expr]) -> list[Param] | None:
    """The cases of one parametrize mark, or None when their number cannot be known."""
    given = {k.arg: k.value for k in call.keywords if k.arg in ("argnames", "argvalues")}
    given |= dict(zip(("argnames", "argvalues"), call.args, strict=False))
    names_node, values = given.get("argnames"), given.get("argvalues")
    if names_node is None or values is None:
        return None
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
        empty: dict[str, ast.expr] = {n: ast.Constant(None) for n in names}  # bound: no fixture
        return [Param(empty, "empty parametrize: collected as one skipped test")]
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


def _marks_of(decorators: list[ast.expr], extra: list[ast.expr]):
    """(parametrize calls, refusal, usefixtures names, count_known) for a decorator list plus
    pytestmark entries."""
    calls: list[ast.Call] = []
    refusal = None
    used: list[str] = []
    known = True
    for deco in [*decorators, *extra]:
        mark = _mark(deco)
        if mark and mark[0] == "usefixtures":
            args = mark[1].args if mark[1] else []
            used += [
                a.value for a in args if isinstance(a, ast.Constant) and isinstance(a.value, str)
            ]
            known = known and all(isinstance(a, ast.Constant) for a in args)
        if mark is None:
            name = ".".join(dotted(deco.func if isinstance(deco, ast.Call) else deco) or ["?"])
            refusal = refusal or f"decorated with {name} (not a plain test)"
        elif mark[0] == "parametrize" and mark[1] is not None:
            calls.append(mark[1])
        else:
            refusal = refusal or f"marked {mark[0]}"
    return calls, refusal, used, known


def collect(
    tree: ast.Module,
    path: str,
    conftests: list[ast.Module],
    scope: Scope,
    inits: list[ast.Module] | None = None,
) -> list[Item]:
    """Every test id pytest would collect from tree (path is how ids are spelt). scope says what
    the configuration and the imported modules around the test file already decided."""
    fixtures = fixtures_of([*conftests, tree])
    consts = module_env(tree)  # a table bound twice is not static
    module_marks = pytestmark_of(tree.body)
    names = _defs(tree)
    base, known = scope.reason, scope.known
    package_hooks = set().union(*(_defs(t) for t in inits or [])) & SETUP_HOOKS
    if package_hooks:
        base = base or f"package init setup hook {sorted(package_hooks)[0]}"
    if fixtures.autouse:
        base = base or f"autouse fixture {fixtures.autouse[0]} is in effect"
    elif names & SETUP_HOOKS:
        base = base or f"module setup hook {sorted(names & SETUP_HOOKS)[0]}"
    items: list[Item] = []
    classes = {n.name: n for n in tree.body if isinstance(n, ast.ClassDef)}
    ctx = Ctx(classes, module_marks, consts, fixtures, [*fixtures.autouse, *scope.usefixtures])
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.startswith(
            "test"
        ):
            items += _expand(node, f"{path}::{node.name}", [], ctx, base)
        elif isinstance(node, ast.ClassDef):
            found, class_known = _class_items(node, path, [], ctx, base)
            items += found
            known = known and class_known
    if not items and not known:  # tests may come from a hook or plugin defined elsewhere
        items.append(Item(f"{path}::<unseen>", 1, _UNSEEN, refusal=UNSEEN, exact=False))
    return [replace(item, exact=item.exact and known) for item in items]


def _defs(tree: ast.Module) -> set[str]:
    return {n.name for n in tree.body if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)}


UNSEEN = "tests here come from code the reader cannot see (count unknown)"
_UNSEEN: ast.FunctionDef = ast.parse("def unseen():\n    pass\n").body[0]  # type: ignore[assignment]


@dataclass
class Scope:
    reason: str | None
    known: bool
    usefixtures: list[str]
    imports: set[str] = field(default_factory=set)  # project modules imported around the tests


@dataclass
class Ctx:
    classes: dict[str, ast.ClassDef]
    module_marks: list[ast.expr]
    consts: dict[str, ast.expr]
    fixtures: Fixtures
    always: list[str]  # fixtures every test here requests: autouse, config usefixtures


def _class_items(node: ast.ClassDef, prefix: str, outer: list, ctx: Ctx, base):
    """(items, count_known) of a class and the test classes nested in it."""
    classes = ctx.classes
    unittest = is_testcase(node, classes)
    methods = methods_of(node, classes)
    has_init = "__init__" in methods  # inherited from a local base too
    known, test_id = not unknown_bases(node, classes), f"{prefix}::{node.name}"
    unseen = Item(f"{test_id}::<unseen>", node.lineno, _UNSEEN, refusal=UNSEEN, exact=False)
    if not known and (node.name.startswith("Test") or any(m.startswith("test") for m in methods)):
        return [unseen], False  # a base defined elsewhere may bring tests: never leave it out
    if not unittest and (not node.name.startswith("Test") or has_init):
        return [], known
    hooks = sorted(set(methods) & CLASS_HOOKS)
    autouse = [m.name for m in methods.values() if is_autouse(m)]
    refusal = base or (f"class setup hook {hooks[0]}" if hooks else None)
    refusal = refusal or (f"class autouse fixture {autouse[0]}" if autouse else None)
    marks = [*outer, *class_marks(node, classes)]
    inner_ctx = replace(ctx, always=[*ctx.always, *autouse])
    items: list[Item] = []
    for name, func in methods.items():
        if name.startswith("test"):
            items += _expand(func, f"{test_id}::{name}", marks, inner_ctx, refusal, method=True)
    for inner in node.body:
        if isinstance(inner, ast.ClassDef):
            found, inner_known = _class_items(inner, test_id, marks, inner_ctx, refusal)
            items += found
            known = known and inner_known
    return items, known


def _expand(func, test_id, outer, ctx: Ctx, refusal, *, method=False) -> list[Item]:
    calls, mark_refusal, usefixtures, marks_known = _marks_of(
        func.decorator_list, [*outer, *ctx.module_marks]
    )
    refusal = refusal or mark_refusal
    if isinstance(func, ast.AsyncFunctionDef):
        refusal = refusal or "async test"
    tables: list[list[Param]] = []
    for call in calls:
        table = parametrize(call, ctx.consts)
        if table is None:
            reason = "parametrize values are not static"
            return [Item(test_id, func.lineno, func, {}, reason, False)]
        tables.append(table)
    bound = {name for table in tables for p in table for name in p.bindings}
    args = [a.arg for a in [*func.args.posonlyargs, *func.args.args, *func.args.kwonlyargs]]
    if method and args:
        args = args[1:]
    used = [a for a in args if a not in bound]
    count, exact = ctx.fixtures.count([*used, *usefixtures, *ctx.always], bound)
    if used:
        refusal = refusal or f"uses fixture(s): {', '.join(used)}"
    return list(_items(func, test_id, tables, count, refusal, exact and marks_known))


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
