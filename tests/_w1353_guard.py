"""Allow-list guard for #1353 witnesses (#1398): an observation must come from a pinned route.

A witness hands ``row()`` (tests/_w1353_probe.py) a raw production return; ``row`` derives the
recorded ``observed`` from it. This guard rejects every ``raw`` (and every assignment to
``observed``) that is not traceably the return of one of ``ROUTES`` — a literal, a builtin or
foreign call, a lambda, a parameter, a module constant — and every way of smuggling an
``observed`` key or calling ``row`` through an alias. Bindings are collected by identifier for the
whole module, so one non-route binding of a name rejects every use of it.
"""

from __future__ import annotations

import ast

# Production entry points the witnesses call, and the probe helpers that wrap verify_cli.
ROUTES = frozenset({"judge_run", "judge_blackbox", "run_blackbox", "_run", "verify_bundle"})
PROBE_ROUTES = frozenset({"verify", "blackbox_result"})
PROBE_MODULE = "_w1353_probe"
_FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef)


def _literal_observations(source, route_predicate=None, *, probe=False):
    """Line numbers of literal or smuggled observations; ``probe`` marks _w1353_probe itself."""
    tree = ast.parse(source)
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    facts = _collect(tree)
    is_route = route_predicate or _route_predicate(facts, probe)
    flagged = _find(tree, parents, facts, probe, is_route)
    if not probe:  # only the probe may define the recorder that builds the "observed" key
        flagged |= {n.lineno for n in tree.body if isinstance(n, _FUNCS) and n.name == "row"}
    return sorted(flagged)


def _collect(tree):
    facts = {"bind": {}, "modules": set(), "names": set(), "exc": set(), "row": {"row"}}
    probe_names = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for alias in n.names:
                if alias.name.split(".")[0].startswith("cairnwork"):
                    facts["modules"].add(alias.asname or alias.name.split(".")[0])
        elif isinstance(n, ast.ImportFrom):
            _collect_from(n, facts, probe_names)
    facts["probe"] = probe_names
    for n in ast.walk(tree):
        for name, value in _bindings_of(n):
            facts["bind"].setdefault(name, []).append(value)
            if isinstance(value, ast.Name) and value.id in facts["row"]:
                facts["row"].add(name)
    return facts


def _collect_from(node, facts, probe_names):
    module = node.module or ""
    for alias in node.names:
        local = alias.asname or alias.name
        if module.split(".")[0].startswith("cairnwork"):
            facts["modules"].add(local)
            if alias.name in ROUTES:
                facts["names"].add(local)
            if alias.name[:1].isupper():  # an exception class such as TaskRejected
                facts["exc"].add(local)
        if module == PROBE_MODULE and alias.name == "row":
            facts["row"].add(local)
        if module == PROBE_MODULE and alias.name in PROBE_ROUTES:
            probe_names.add(local)


def _bindings_of(n):
    if isinstance(n, ast.Assign):
        return [(name, n.value) for t in n.targets for name in _names(t)]
    if isinstance(n, (ast.AnnAssign, ast.NamedExpr, ast.AugAssign)):
        return [(name, n.value) for name in _names(n.target)]
    if isinstance(n, (ast.For, ast.AsyncFor)):
        return [(name, n.iter) for name in _names(n.target)]
    if isinstance(n, (ast.With, ast.AsyncWith)):
        return [
            (name, None) for i in n.items if i.optional_vars for name in _names(i.optional_vars)
        ]
    if isinstance(n, ast.ExceptHandler) and n.name:
        return [(n.name, n)]
    if isinstance(n, _FUNCS):
        return [(n.name, n)]
    return []


def _route_predicate(facts, probe):
    bind = facts["bind"]

    def call_ok(node, stack):
        fn = node.func
        if isinstance(fn, ast.Attribute):
            owner = fn.value
            return (
                isinstance(owner, ast.Name) and owner.id in facts["modules"] and fn.attr in ROUTES
            )
        if not isinstance(fn, ast.Name):
            return False
        if fn.id in facts["names"] or fn.id in facts["probe"]:
            return True
        defs = [v for v in bind.get(fn.id, []) if isinstance(v, _FUNCS)]
        if probe and fn.id in PROBE_ROUTES and defs:
            return True
        return (
            bool(defs) and fn.id not in stack and all(returns_ok(d, stack | {fn.id}) for d in defs)
        )

    def returns_ok(fn_def, stack):
        rets = [r for r in ast.walk(fn_def) if isinstance(r, ast.Return)]
        return bool(rets) and all(r.value is not None and value_ok(r.value, stack) for r in rets)

    def binding_ok(value, stack):
        if isinstance(value, ast.ExceptHandler):
            kind = value.type
            return isinstance(kind, ast.Name) and kind.id in facts["exc"]
        return value is not None and not isinstance(value, _FUNCS) and value_ok(value, stack)

    def value_ok(node, stack=frozenset()):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute) and node.func.attr == "get":
                return value_ok(node.func.value, stack)
            return call_ok(node, stack)
        if isinstance(node, ast.Name):
            entries = bind.get(node.id, [])
            return bool(entries) and all(binding_ok(v, stack) for v in entries)
        if isinstance(node, ast.Attribute):
            return value_ok(node.value, stack)
        if isinstance(node, ast.Subscript):
            return isinstance(node.slice, ast.Constant) and value_ok(node.value, stack)
        return False

    return value_ok


def _in_probe_row(node, parents, probe):
    """True only inside the probe's own module-level ``def row`` (not a nested one)."""
    while node in parents and not isinstance(node, _FUNCS):
        node = parents[node]
    if not (probe and isinstance(node, ast.FunctionDef) and node.name == "row"):
        return False
    return isinstance(parents.get(node), ast.Module)


def _find(tree, parents, facts, probe, is_route):
    flagged = set()
    rows = facts["row"]
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and _is_row_call(n, rows):
            raw = (
                n.args[3]
                if len(n.args) > 3
                else next((kw.value for kw in n.keywords if kw.arg == "raw"), None)
            )
            if raw is None or not is_route(raw):
                flagged.add(n.lineno)
        if isinstance(n, (ast.Assign, ast.AnnAssign, ast.NamedExpr, ast.AugAssign)):
            targets = n.targets if isinstance(n, ast.Assign) else [n.target]
            if any("observed" in _names(t) for t in targets):
                if not _in_probe_row(n, parents, probe) and not is_route(n.value):
                    flagged.add(n.lineno)
        if isinstance(n, ast.Dict):
            for key, value in zip(n.keys, n.values, strict=True):
                if isinstance(key, ast.Constant) and key.value == "observed":
                    bare = isinstance(value, ast.Name) and value.id == "observed"
                    if not (bare and _in_probe_row(n, parents, probe)):
                        flagged.add(n.lineno)
        flagged |= _smuggled(n, parents, rows)
    return flagged


def _smuggled(n, parents, rows):
    if isinstance(n, ast.Call):
        if any(k.arg == "observed" for k in n.keywords):
            return {n.lineno}
        fn = n.func
        if isinstance(fn, ast.Name) and fn.id in {"getattr", "hasattr", "setattr"}:
            key = n.args[1] if len(n.args) > 1 else None
            if isinstance(key, ast.Constant) and key.value in {"row", "observed"}:
                return {n.lineno}
    if isinstance(n, ast.Subscript) and isinstance(n.ctx, ast.Store):
        if isinstance(n.slice, ast.Constant) and n.slice.value == "observed":
            return {n.lineno}
    aliased = (isinstance(n, ast.Name) and n.id in rows) or (
        isinstance(n, ast.Attribute) and n.attr == "row"
    )
    if aliased and isinstance(n, (ast.Name, ast.Attribute)) and isinstance(n.ctx, ast.Load):
        parent = parents.get(n)
        if not (isinstance(parent, ast.Call) and parent.func is n):
            return {n.lineno}
    return set()


def _names(target):
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, (ast.Tuple, ast.List)):
        return set().union(*(_names(item) for item in target.elts))
    if isinstance(target, ast.Starred):
        return _names(target.value)
    return set()


def _is_row_call(call, rows):
    fn = call.func
    return (isinstance(fn, ast.Name) and fn.id in rows) or (
        isinstance(fn, ast.Attribute) and fn.attr == "row"
    )
