"""tools/blackbox_suggest (#370): a project module that may rebind another module's names.

The reader takes a target's last plain binding in its own module. A project module that the
drafted targets import can still change that binding, or a global the target reads, from
outside: a store or delete on an attribute of an imported name (its own module imported by
name included), a project module passed on as a value and changed where the reader does not
look, or a patcher. A value taken from a project module (by import, by star import, through
an assignment, a loop, a call argument or a return) and then changed in place by a store, a
delete or a mutating method can change it too. Any of these in such a module refuses the draft.
Every other use of such a value must be a known-safe one (escape.py); any other refuses.
"""

from __future__ import annotations

import ast
import builtins
from collections.abc import Callable

from .loaders import MOCK

PATCHERS = (*MOCK, "_pytest.monkeypatch", "pytest.MonkeyPatch")  # imported in any form
MUTATORS = {
    "update", "setdefault", "pop", "popitem", "clear", "append", "extend", "insert", "remove",
    "add", "discard", "sort", "reverse", "__setitem__", "__delitem__", "__iadd__", "__ior__",
}  # fmt: skip


def rebinds_modules(
    tree: ast.Module, absolute: Callable[[ast.ImportFrom], str], is_module: Callable[[str], bool]
) -> str | None:
    """Why a module may rebind another module's attributes, or None. absolute gives the
    absolute module of a from-import; is_module tells a project module by its dotted name."""
    imported: set[str] = set()  # every name an import statement binds, in any scope
    modules: set[str] = set()  # dotted names surely bound to a project module object
    seeds: set[str] = set()  # names bound to a project module or to a value taken from one
    star = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _patcher(alias.name):
                    return "it imports a patcher"
                bound = alias.asname or alias.name.split(".")[0]
                imported.add(bound)
                parts = alias.name.split(".")
                if is_module(parts[0]):
                    seeds.add(bound)
                    modules |= (
                        {bound}
                        if alias.asname
                        else {".".join(parts[: i + 1]) for i in range(len(parts))}
                    )
        elif isinstance(node, ast.ImportFrom):
            base = absolute(node)
            for alias in node.names:
                if _patcher(f"{base}.{alias.name}") or _patcher(base):
                    return "it imports a patcher"
                bound = alias.asname or alias.name
                imported.add(bound)
                if alias.name == "*":
                    star = star or is_module(base)
                    continue
                if is_module(f"{base}.{alias.name}"):
                    modules.add(bound)
                if is_module(base) or is_module(f"{base}.{alias.name}"):
                    seeds.add(bound)
    derived = _derived(tree, seeds | (_unbound(tree) if star else set()))
    parents = {id(child): node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute | ast.Subscript) and isinstance(
            node.ctx, ast.Store | ast.Del
        ):
            chain = _chain(node.value)
            if isinstance(node, ast.Attribute) and chain & (imported | {f".{n}" for n in imported}):
                return "it stores an attribute on an imported name"
            if _changes(node.value, derived):
                return "it stores into a value taken from a project module"
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            receiver = node.func.value
            if node.func.attr in MUTATORS and _dotted(receiver) not in modules:
                if _changes(receiver, derived):
                    return "it changes a value taken from a project module in place"
        if _module_value(node, modules, parents.get(id(node))):
            return "it passes a project module on as a value"
    from .escape import escapes  # built on this module's helpers, so imported here

    if escapes(tree, derived, seeds | (_unbound(tree) if star else set()), parents):
        return "it hands a value taken from a project module to a use this reader does not follow"
    return None


def _changes(receiver: ast.expr, derived: set[str]) -> bool:
    """A store or mutation on this receiver reaches a value taken from a project module: the
    chain passes a holder of one, or goes into an element of a new container holding one."""
    chain = _chain(receiver)
    boxes = {m[2:] for m in derived if m.startswith("[]")}
    return bool(chain & derived) or bool((chain & boxes) - {_mark(receiver)})


def _derived(tree: ast.Module, seeds: set[str]) -> set[str]:
    """Holders of a value taken from a seed, in any scope: names, .attr for attribute holders,
    functions returning one and parameters passed one; []name for a new container holding one."""
    derived = set(seeds)
    functions: dict[str, list[ast.FunctionDef | ast.AsyncFunctionDef]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            functions.setdefault(node.name, []).append(node)
    size = -1
    while size != len(derived):
        size = len(derived)
        for node in ast.walk(tree):
            for targets, value, element in _bindings(node):
                kind = _kind(value, derived, element)
                if kind is not None:
                    derived |= {kind + n for t in targets for n in _target_names(t)}
            if isinstance(node, ast.Call):
                for function in functions.get(_callee(node.func) or "", []):
                    derived |= _given(function, node, derived)
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                if any(_kind(v, derived, True) is not None for v in _returns(node)):
                    derived.add(node.name)
    return derived


def _kind(value: ast.expr, derived: set[str], element: bool) -> str | None:
    """ "" when the value may be a held object, "[]" when it may be a new container holding
    one, None otherwise. element: the target takes the value's elements (a loop, unpacking)."""
    marks = _held(value)
    direct = {m for m in marks if not m.startswith("[]")}
    inner = {m[2:] for m in marks if m.startswith("[]")}
    held = {m for m in derived if not m.startswith("[]")}
    boxes = {m[2:] for m in derived if m.startswith("[]")}
    if direct & held:
        return ""
    if direct & boxes or inner & (held | boxes):
        return "" if element else "[]"
    return None


def _bindings(node: ast.AST) -> list[tuple[list[ast.expr], ast.expr, bool]]:
    """(targets, value, element) for what a statement or expression binds; element when the
    targets take the value's elements."""
    if isinstance(node, ast.Assign):
        pairs = _pairs(node.targets, node.value)
        if pairs:
            return pairs
        unpack = any(isinstance(t, ast.Tuple | ast.List) for t in node.targets)
        return [(node.targets, node.value, unpack)]
    if isinstance(node, ast.AnnAssign | ast.AugAssign) and node.value is not None:
        return [([node.target], node.value, False)]
    if isinstance(node, ast.NamedExpr):
        return [([node.target], node.value, False)]
    if isinstance(node, ast.For | ast.AsyncFor | ast.comprehension):
        return [([node.target], node.iter, True)]
    if isinstance(node, ast.With | ast.AsyncWith):
        return [([i.optional_vars], i.context_expr, False) for i in node.items if i.optional_vars]
    return []


def _pairs(targets: list[ast.expr], value: ast.expr) -> list[tuple[list[ast.expr], ast.expr, bool]]:
    """a, b = x, y binds a to x and b to y; empty for any other shape."""
    target = targets[0] if len(targets) == 1 else None
    if not isinstance(target, ast.Tuple | ast.List) or not isinstance(value, ast.Tuple | ast.List):
        return []
    left, right = target.elts, value.elts
    if len(left) != len(right) or any(isinstance(e, ast.Starred) for e in [*left, *right]):
        return []
    return [([t], v, False) for t, v in zip(left, right, strict=True)]


def _target_names(target: ast.expr) -> set[str]:
    """Names a target binds, and .attr for an attribute target: self.k = v makes .k a holder."""
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, ast.Attribute):
        return {f".{target.attr}"}
    if isinstance(target, ast.Starred):
        return _target_names(target.value)
    if isinstance(target, ast.Tuple | ast.List):
        return {n for item in target.elts for n in _target_names(item)}
    return set()


def _given(
    function: ast.FunctionDef | ast.AsyncFunctionDef, call: ast.Call, derived: set[str]
) -> set[str]:
    """The parameters of a function that a call passes a held value or container to."""
    spec = function.args
    params = [*spec.posonlyargs, *spec.args]
    method = isinstance(call.func, ast.Attribute) and params and params[0].arg in {"self", "cls"}
    every = {a.arg for a in [*params, *spec.kwonlyargs, spec.vararg, spec.kwarg] if a}
    found: set[str] = set()
    for i, arg in enumerate(call.args):
        kind = _kind(arg.value if isinstance(arg, ast.Starred) else arg, derived, False)
        if kind is None:
            continue
        index = i + 1 if method else i
        starred = isinstance(arg, ast.Starred) or index >= len(params)
        found |= {kind + n for n in (every if starred else {params[index].arg})}
    for keyword in call.keywords:
        kind = _kind(keyword.value, derived, False)
        if kind is not None:
            names = {keyword.arg} if keyword.arg else every
            names |= {spec.kwarg.arg} if spec.kwarg else set()
            found |= {kind + n for n in names}
    return found


def _returns(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.expr]:
    return [
        n.value
        for n in ast.walk(fn)
        if isinstance(n, ast.Return | ast.Yield | ast.YieldFrom) and n.value is not None
    ]


def _callee(func: ast.expr) -> str | None:  # the function name a call names, if any
    if isinstance(func, ast.Name):
        return func.id
    return func.attr if isinstance(func, ast.Attribute) else None


PASS_ON = (ast.IfExp, ast.BoolOp, ast.NamedExpr, ast.Starred, ast.Await)  # result is a part
DISPLAYS = (
    *(ast.List, ast.Tuple, ast.Set, ast.Dict),
    *(ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp),
)


def _held(node: ast.expr) -> set[str]:
    """Marks for what a value may be: a name, .attr, or []mark for an element of a new
    container. Other results (a call, arithmetic, a string) are new values that hold nothing."""
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, ast.Attribute):
        return {f".{node.attr}"} | _held(node.value)
    if isinstance(node, ast.Subscript):
        return _held(node.value)
    if isinstance(node, PASS_ON):
        parts = [c for c in ast.iter_child_nodes(node) if isinstance(c, ast.expr)]
        return {m for part in parts for m in _held(part)}
    if isinstance(node, DISPLAYS):  # a nested container stays a container holding it
        marks = {m for part in _elements(node) for m in _held(part)}
        return {m if m.startswith("[]") else f"[]{m}" for m in marks}
    return set()


def _elements(node: ast.expr) -> list[ast.expr]:
    """The values a new container holds: items, dict keys and values, a comprehension's result."""
    if isinstance(node, ast.List | ast.Tuple | ast.Set):
        return list(node.elts)
    if isinstance(node, ast.Dict):
        return [*(k for k in node.keys if k is not None), *node.values]
    if isinstance(node, ast.DictComp):
        return [node.key, node.value]
    return [node.elt] if isinstance(node, ast.ListComp | ast.SetComp | ast.GeneratorExp) else []


def _mark(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    return f".{node.attr}" if isinstance(node, ast.Attribute) else None


def _unbound(tree: ast.Module) -> set[str]:
    """Names read but bound nowhere in the module: after a star import they may come from it."""
    bound = set(dir(builtins))
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Load):
            bound.add(node.id)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            bound.add(node.name)
        elif isinstance(node, ast.arg):
            bound.add(node.arg)
        elif isinstance(node, ast.alias) and node.name != "*":
            bound.add((node.asname or node.name).split(".")[0])
        elif isinstance(node, ast.ExceptHandler | ast.MatchAs | ast.MatchStar) and node.name:
            bound.add(node.name)
    return {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} - bound


def _patcher(name: str) -> bool:
    return any(name == p or name.startswith(f"{p}.") for p in PATCHERS)


def _chain(node: ast.expr) -> set[str]:
    """The root name and every .attr along a receiver chain: K.m.SCALE = 10 gives K and .m."""
    names: set[str] = set()
    while isinstance(node, ast.Attribute | ast.Subscript | ast.Call):
        if isinstance(node, ast.Attribute):
            names.add(f".{node.attr}")
        node = node.func if isinstance(node, ast.Call) else node.value
    return names | ({node.id} if isinstance(node, ast.Name) else set())


def _dotted(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        head = _dotted(node.value)
        return None if head is None else f"{head}.{node.attr}"
    return None


def _module_value(node: ast.AST, modules: set[str], parent: ast.AST | None) -> bool:
    """A load of a project module object that is not the receiver of an attribute lookup."""
    if not isinstance(node, ast.Name | ast.Attribute) or not isinstance(node.ctx, ast.Load):
        return False
    if isinstance(parent, ast.Attribute) and parent.value is node:
        return False
    return _dotted(node) in modules
