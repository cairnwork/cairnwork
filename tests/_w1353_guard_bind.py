"""Binding and route-flow analysis for the #1353 witness guard."""

from __future__ import annotations

import ast

ROUTES = frozenset({"judge_run", "judge_blackbox", "run_blackbox", "_run", "verify_bundle"})
PROBE_ROUTES = frozenset({"verify", "blackbox_result"})
PROBE_MODULE = "_w1353_probe"
PACKAGES = frozenset(
    "cairnwork cairnwork_attest cairnwork_sandbox cairnwork_protocol cairnwork_quickstart".split()
)
FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef)


def is_package(name, level=0):
    return level == 0 and name.split(".")[0] in PACKAGES


def collect(tree, parents):
    facts = {
        "bind": {},
        "routes": {},
        "probes": {},
        "modules": {},
        "exc": set(),
        "row": {"row"},
        "tree": tree,
    }

    def add(name, value, kind):
        facts["bind"].setdefault(name, []).append((kind, value))

    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for alias in n.names:
                local = alias.asname or alias.name.split(".")[0]
                add(local, (n, alias), "module")
                if is_package(alias.name):
                    facts["modules"].setdefault(local, []).append((n, alias))
        elif isinstance(n, ast.ImportFrom):
            module, package = n.module or "", is_package(n.module or "", n.level)
            for alias in n.names:
                local = alias.asname or alias.name
                add(local, (n, alias), "import")
                if package:
                    facts["modules"].setdefault(local, []).append((n, alias))
                if package and alias.name in ROUTES:
                    facts["routes"].setdefault(local, []).append((n, alias))
                if package and alias.name[:1].isupper():
                    facts["exc"].add(local)
                if module == PROBE_MODULE and n.level == 0 and alias.name in PROBE_ROUTES:
                    facts["probes"].setdefault(local, []).append((n, alias))
                if module.startswith("_w1353_") and alias.name == "row":
                    facts["row"].add(local)
        elif isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            parent = parents.get(n)
            target = n
            while isinstance(parents.get(target), (ast.Tuple, ast.List, ast.Starred)):
                target = parents[target]
            parent = parents.get(target)
            value = (
                parent.value
                if isinstance(parent, (ast.Assign, ast.AnnAssign, ast.NamedExpr, ast.AugAssign))
                else None
            )
            if value is None and isinstance(parent, (ast.For, ast.AsyncFor, ast.comprehension)):
                value = parent.iter
            add(n.id, value, "value" if value is not None else "other")
        elif isinstance(n, FUNCS):
            add(n.name, n, "function")
            for arg in _args(n.args):
                add(arg.arg, None, "other")
        elif isinstance(n, ast.Lambda):
            for arg in _args(n.args):
                add(arg.arg, None, "other")
        elif isinstance(n, ast.ClassDef):
            add(n.name, n, "class")
        elif isinstance(n, ast.ExceptHandler) and n.name:
            add(n.name, n, "except")
        elif isinstance(n, ast.MatchAs) and n.name:
            add(n.name, n, "other")
        elif isinstance(n, ast.MatchStar) and n.name:
            add(n.name, n, "other")
        elif isinstance(n, ast.MatchMapping) and n.rest:
            add(n.rest, n, "other")
    for name, records in facts["bind"].items():
        if any(
            kind == "value" and isinstance(v, ast.Name) and v.id in facts["row"]
            for kind, v in records
        ):
            facts["row"].add(name)
    return facts


def _args(args):
    return [
        *args.posonlyargs,
        *args.args,
        *args.kwonlyargs,
        *([args.vararg] if args.vararg else []),
        *([args.kwarg] if args.kwarg else []),
    ]


def trusted_import(facts, group, name, predicate):
    candidates, bindings = group.get(name, []), facts["bind"].get(name, [])
    return (
        bool(candidates)
        and len(candidates) == len(bindings)
        and all(
            kind == "import" and predicate(*candidate)
            for kind, candidate in zip((item[0] for item in bindings), candidates, strict=True)
        )
    )


def trusted_module(facts, name):
    candidates, bindings = facts["modules"].get(name, []), facts["bind"].get(name, [])
    return (
        bool(candidates)
        and len(candidates) == len(bindings)
        and all(
            (
                kind == "module"
                and is_package(alias.name)
                or kind == "import"
                and is_package(statement.module or "", statement.level)
            )
            for kind, (statement, alias) in bindings
        )
    )


def _enclosing(node, parents):
    while node in parents:
        node = parents[node]
        if isinstance(node, FUNCS):
            return node
    return None


def _local_module_import(name, fn, parents):
    if fn is None:
        return False
    scoped = [node for node in ast.walk(fn) if node is fn or _enclosing(node, parents) is fn]
    imported = any(
        isinstance(node, ast.Import)
        and any(
            (alias.asname or alias.name.split(".")[0]) == name and is_package(alias.name)
            for alias in node.names
        )
        or isinstance(node, ast.ImportFrom)
        and any(
            (alias.asname or alias.name) == name and is_package(node.module or "", node.level)
            for alias in node.names
        )
        for node in scoped
    )
    rebound = any(
        isinstance(node, ast.Name)
        and node.id == name
        and isinstance(node.ctx, (ast.Store, ast.Del))
        for node in scoped
    )
    return imported and not rebound
