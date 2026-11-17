"""Route-flow analysis for the #1353 witness guard."""

from __future__ import annotations

import ast
from typing import Any

from _w1353_guard_bind import (
    FUNCS,
    PROBE_MODULE,
    PROBE_ROUTES,
    ROUTES,
    _args,
    _local_module_import,
    is_package,
    trusted_import,
    trusted_module,
)


def _assigned_value(parent: ast.AST | None) -> ast.expr | None:
    """The value a store target receives from its parent statement, if any."""
    if isinstance(parent, (ast.Assign, ast.AnnAssign, ast.NamedExpr, ast.AugAssign)):
        return parent.value
    if isinstance(parent, (ast.For, ast.AsyncFor, ast.comprehension)):
        return parent.iter
    return None


def _all_params(args: ast.arguments) -> list[ast.arg]:
    return [*args.posonlyargs, *args.args, *args.kwonlyargs]


def _actual_argument(call: ast.Call, index: int, positional: int, name: str) -> ast.expr | None:
    """The expression a call passes for one parameter (positional first, then keyword)."""
    arg = call.args[index] if index < positional and index < len(call.args) else None
    if arg is None:
        arg = next((k.value for k in call.keywords if k.arg == name), None)
    return arg


def _unpackable(call: ast.Call) -> bool:
    return any(k.arg is None for k in call.keywords) or any(
        isinstance(a, ast.Starred) for a in call.args
    )


class _Router:
    """Decides whether an expression is derived from a pinned route return value."""

    def __init__(self, facts: dict[str, Any], probe: bool) -> None:
        self.facts = facts
        self.probe = probe
        self.bind = facts["bind"]
        module = facts.get("tree")
        assert isinstance(module, ast.Module)
        self.module = module
        self.parents: dict[ast.AST, ast.AST] = {
            child: parent for parent in ast.walk(module) for child in ast.iter_child_nodes(parent)
        }
        self.function_cache: dict[tuple[str, int], bool] = {}

    def enclosing_function(self, node: ast.AST) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
        while node in self.parents:
            node = self.parents[node]
            if isinstance(node, FUNCS):
                return node
        return None

    def local_records(self, fn, name):
        if fn is None:
            return []
        found = []
        for node in ast.walk(fn):
            if not (node is fn or self.enclosing_function(node) is fn):
                continue
            if (
                isinstance(node, ast.Name)
                and node.id == name
                and isinstance(node.ctx, (ast.Store, ast.Del))
            ):
                target: ast.AST = node
                while isinstance(self.parents.get(target), (ast.Tuple, ast.List, ast.Starred)):
                    target = self.parents[target]
                value = _assigned_value(self.parents.get(target))
                found.append(("value" if value is not None else "other", value))
        for arg in _args(fn.args):
            if arg.arg == name:
                found.append(("parameter", arg))
        return found

    def callback_function(self, node, context, stack):
        if not isinstance(node, ast.Name):
            return False
        if context and any(arg.arg == node.id for arg in _args(context.args)):
            return self.callback_parameter(context, node.id, stack)
        defs = [value for kind, value in self.bind.get(node.id, []) if kind == "function"]
        return len(defs) == 1 and self.local_function(node.id, defs[0], stack)

    def callback_argument_ok(self, node, stack):
        return self.callback_function(node, self.enclosing_function(node), stack)

    def callback_parameter(self, fn, param, stack):
        params = _all_params(fn.args)
        if fn.args.vararg or fn.args.kwarg or fn.decorator_list:
            return False
        index = next((i for i, item in enumerate(params) if item.arg == param), None)
        if index is None:
            return False
        uses, calls = _direct_uses(self.module, fn.name)
        if not calls or len(uses) != len(calls):
            return False
        positional = len([*fn.args.posonlyargs, *fn.args.args])
        for call in calls:
            if _unpackable(call):
                return False
            arg = _actual_argument(call, index, positional, param)
            if arg is None or not self.callback_argument_ok(arg, stack):
                return False
        return True

    def module_value(self, node, context, stack):
        if not isinstance(node, ast.Name):
            return False
        if trusted_module(self.facts, node.id) or _local_module_import(
            node.id, context, self.parents
        ):
            return True
        if context is None or not any(arg.arg == node.id for arg in _args(context.args)):
            return False
        params = _all_params(context.args)
        index = next((i for i, arg in enumerate(params) if arg.arg == node.id), None)
        if index is None or context.args.vararg or context.args.kwarg:
            return False
        uses, calls = _direct_uses(self.module, context.name)
        if not calls or len(uses) != len(calls):
            return False
        positional = len([*context.args.posonlyargs, *context.args.args])
        for call in calls:
            if _unpackable(call):
                return False
            arg = _actual_argument(call, index, positional, node.id)
            if arg is None or not self.module_value(arg, self.enclosing_function(call), stack):
                return False
        return True

    def value_ok(self, node, stack=frozenset(), context=None):
        if isinstance(node, ast.Call):
            return self._call_ok(node, stack, context)
        if isinstance(node, ast.Name):
            return self._name_ok(node, stack, context)
        if isinstance(node, ast.Attribute):
            return self.value_ok(node.value, stack, context)
        if isinstance(node, ast.Subscript):
            return isinstance(node.slice, ast.Constant) and self.value_ok(
                node.value, stack, context
            )
        return False

    def _call_ok(self, node, stack, context):
        fn = node.func
        if isinstance(fn, ast.Attribute) and fn.attr == "get":
            return self.value_ok(fn.value, stack, context)
        if isinstance(fn, ast.Attribute):
            return fn.attr in ROUTES and self.module_value(
                fn.value, context or self.enclosing_function(node), stack
            )
        if not isinstance(fn, ast.Name):
            return False
        if _import_route(self.facts, fn.id, True):
            return True
        if self.probe and fn.id in PROBE_ROUTES and _single_local_function(fn.id, self.bind):
            return True
        if context and any(arg.arg == fn.id for arg in _args(context.args)):
            return self.callback_parameter(context, fn.id, stack)
        return self.local_call(fn.id, node, stack, context)

    def _name_ok(self, node, stack, context):
        if _import_route(self.facts, node.id, True):
            return True
        fn = context or self.enclosing_function(node)
        records = self.local_records(fn, node.id) or self.bind.get(node.id, [])
        return bool(records) and all(
            self._record_ok(node, fn, kind, v, stack) for kind, v in records
        )

    def _record_ok(self, node, fn, kind, v, stack):
        if kind == "except":
            return isinstance(v.type, ast.Name) and v.type.id in self.facts["exc"]
        if kind == "value":
            return v is not None and self.value_ok(v, stack, fn)
        if kind == "function":
            return self.local_function(node.id, v, stack)
        if kind == "parameter":
            return _parameter_sites(
                node.id, fn, v.arg, v.arg, self.value_ok, self.module, stack
            ) or self.callback_parameter(fn, v.arg, stack)
        return False

    def local_function(self, name, fn, stack):
        key = (name, id(fn))
        if key in self.function_cache:
            return self.function_cache[key]
        self.function_cache[key] = False
        if fn.decorator_list:
            return False
        returns = [
            x
            for x in ast.walk(fn)
            if isinstance(x, ast.Return) and self.enclosing_function(x) is fn
        ]
        if not returns:
            return False
        seen_nonrecursive = False
        for ret in returns:
            verdict = self._return_ok(name, fn, ret, stack)
            if verdict is False:
                return False
            seen_nonrecursive = seen_nonrecursive or verdict is True
        self.function_cache[key] = seen_nonrecursive
        return seen_nonrecursive

    def _return_ok(self, name, fn, ret, stack):
        """True: routed return; None: self-recursive return (neutral); False: not routed."""
        value = ret.value
        if value is None:
            return False
        if isinstance(value, ast.Call) and _is_row(value):
            raw = value.args[3] if len(value.args) > 3 else None
            return raw is not None and self.value_ok(raw, stack | {name}, fn)
        if self.value_ok(value, stack | {name}, fn):
            return True
        if isinstance(value, ast.Call) and isinstance(value.func, ast.Name):
            return None if value.func.id == name else False
        return False

    def local_call(self, name, call, stack, context):
        context = context or self.enclosing_function(call)
        if context and any(arg.arg == name for arg in _args(context.args)):
            return self.callback_parameter(context, name, stack)
        scoped = (
            [
                node
                for node in ast.walk(context)
                if isinstance(node, FUNCS)
                and node is not context
                and node.name == name
                and self.enclosing_function(node) is context
            ]
            if context
            else []
        )
        records = [("function", scoped[0])] if len(scoped) == 1 else self.bind.get(name, [])
        if len(records) != 1 or records[0][0] != "function":
            return False
        fn = records[0][1]
        if not self.local_function(name, fn, stack):
            return False
        if scoped and not any(_parameter_is_raw(fn, arg.arg) for arg in _args(fn.args)):
            return True
        return _call_arguments_route(name, fn, call, self.value_ok, self.module, stack, context)


def route_predicate(facts, probe):
    return _Router(facts, probe).value_ok


def _import_route(facts, name, probe):
    return trusted_import(
        facts,
        facts["routes"],
        name,
        lambda m, a: is_package(m.module or "", m.level) and a.name in ROUTES,
    ) or (
        probe
        and trusted_import(
            facts,
            facts["probes"],
            name,
            lambda m, a: m.module == PROBE_MODULE and a.name in PROBE_ROUTES,
        )
    )


def _single_local_function(name, bindings):
    return len(bindings.get(name, [])) == 1 and bindings[name][0][0] == "function"


def _parameter_used(fn, name):
    return any(
        isinstance(n, ast.Name) and n.id == name and isinstance(n.ctx, ast.Load)
        for n in ast.walk(fn)
    )


def _parameter_is_raw(fn, name):
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and _is_row(node) and len(node.args) > 3:
            if isinstance(node.args[3], ast.Name) and node.args[3].id == name:
                return True
    return False


def _is_row(call):
    return (
        isinstance(call.func, ast.Name)
        and call.func.id == "row"
        or (isinstance(call.func, ast.Attribute) and call.func.attr == "row")
    )


def _parameter_sites(name, fn, index, param, value_ok, module, stack=frozenset()):
    if fn is None or fn.args.vararg or fn.args.kwarg:
        return False
    uses, calls = _direct_uses(module, name)
    if len(calls) != len(uses) or not calls:
        return False
    positional = [*fn.args.posonlyargs, *fn.args.args]
    defaults = [None] * (len(positional) - len(fn.args.defaults)) + list(fn.args.defaults)
    default = defaults[index] if index < len(positional) else None
    if default is None and index >= len(positional):
        kw_index = index - len(positional)
        default = fn.args.kw_defaults[kw_index]
    if default is not None and _parameter_used(fn, param) and not value_ok(default, stack, fn):
        return False
    for call in calls:
        if any(k.arg is None for k in call.keywords):
            return False
        if any(isinstance(arg, ast.Starred) for arg in call.args):
            return False
        if index < len(positional):
            arg = call.args[index] if index < len(call.args) else None
            if arg is None:
                arg = next((k.value for k in call.keywords if k.arg == param), None)
        else:
            arg = next((k.value for k in call.keywords if k.arg == param), None)
        if arg is None:
            if default is None or not value_ok(default, stack):
                return False
        elif not value_ok(arg, stack):
            return False
    return True


def _call_arguments_route(name, fn, call, value_ok, module, stack, context):
    uses, calls = _direct_uses(module, name)
    if not calls or len(calls) != len(uses):
        return False
    params = [*fn.args.posonlyargs, *fn.args.args, *fn.args.kwonlyargs]
    if any(_parameter_used(fn, p.arg) for p in (fn.args.vararg, fn.args.kwarg) if p):
        return False
    for i, param in enumerate(params):
        if not _parameter_is_raw(fn, param.arg):
            continue
        if not _parameter_sites(name, fn, i, param.arg, value_ok, module, stack):
            return False
    return True


def _direct_uses(module, name):
    parents = {
        child: parent for parent in ast.walk(module) for child in ast.iter_child_nodes(parent)
    }
    uses = [
        n
        for n in ast.walk(module)
        if isinstance(n, ast.Name) and n.id == name and isinstance(n.ctx, ast.Load)
    ]
    calls = []
    for n in uses:
        parent = parents.get(n)
        if isinstance(parent, ast.Call) and parent.func is n:
            calls.append(parent)
    return uses, calls
