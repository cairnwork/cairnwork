"""Fail-closed syntax checks for #1353 witness observations."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, cast

from tests._w1353_allow_imports import literal_string, target_names

ORACLES = {
    ("_w1353_probe", "verify"),
    ("cairnwork.blackbox_judge", "judge_run"),
    ("cairnwork.verify_cli", "_run"),
    ("cairnwork_attest.verify", "verify_bundle"),
}
ADAPTERS: set[tuple[str, str]] = {("_w1353_extra", "cli_route"), ("_w1353_receipt", "bundle_route")}
FORBIDDEN = {
    "exec",
    "eval",
    "compile",
    "getattr",
    "setattr",
    "delattr",
    "vars",
    "globals",
    "locals",
    "__import__",
    "import_module",
    "attrgetter",
    "loads",
    "__getattribute__",
}
BLOCKED_NODES = {ast.Global, ast.Nonlocal, ast.Lambda}
BLOCK_NESTED_IMPORTS = True


def _binders(tree: ast.Module) -> dict[str, list[ast.AST]]:
    found: dict[str, list[ast.AST]] = {}
    for n in ast.walk(tree):
        names = (
            {n.id} if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)) else set()
        )
        if isinstance(n, ast.arg):
            names.add(n.arg)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(n.name)
        elif isinstance(n, ast.Import):
            names.update(a.asname or a.name.split(".")[0] for a in n.names)
        elif isinstance(n, ast.ImportFrom):
            names.update(a.asname or a.name for a in n.names)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            names.add(n.name)
        elif isinstance(n, ast.MatchAs) and n.name:
            names.add(n.name)
        elif isinstance(n, ast.MatchStar) and n.name:
            names.add(n.name)
        elif isinstance(n, ast.MatchMapping) and n.rest:
            names.add(n.rest)
        for name in names:
            found.setdefault(name, []).append(n)
    return found


def _module_oracles(
    tree: ast.Module, binders: dict[str, list[ast.AST]], path: str
) -> tuple[set[str], dict[str, str], set[str], set[str]]:
    oracle_names: set[str] = set()
    module_aliases: dict[str, str] = {}
    row_names: set[str] = set()
    trusted_rows: set[str] = set()
    for n in tree.body:
        if isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
            for a in n.names:
                local = a.asname or a.name
                if (n.module, a.name) in ORACLES:
                    oracle_names.add(local)
                if n.module.startswith("_w1353_") and a.name == "row":
                    row_names.add(local)
                    if n.module == "_w1353_probe":
                        trusted_rows.add(local)
        elif isinstance(n, ast.Import):
            for a in n.names:
                module = a.name
                local = a.asname or module.split(".")[0]
                bound_module = module if a.asname or "." not in module else module.split(".")[0]
                module_aliases[local] = bound_module
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "row":
            row_names.add("row")
            if Path(path).stem == "_w1353_probe":
                trusted_rows.add("row")
    return oracle_names, module_aliases, row_names, trusted_rows


def _valid_adapters(
    tree: ast.Module,
    path: str,
    names: set[str],
    modules: dict[str, str],
    binders: dict[str, list[ast.AST]],
) -> set[str]:
    allowed = {func for stem, func in ADAPTERS if stem == Path(path).stem}
    return {
        fn.name
        for fn in tree.body
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
        and fn.name in allowed
        and _adapter_valid(fn, tree, names, modules, binders)
    }


def _adapter_valid(
    fn: ast.FunctionDef | ast.AsyncFunctionDef,
    tree: ast.Module,
    names: set[str],
    modules: dict[str, str],
    binders: dict[str, list[ast.AST]],
) -> bool:
    if fn.decorator_list or fn.args.kwarg is not None or len(binders.get(fn.name, [])) != 1:
        return False
    if any(
        isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda))
        and n is not fn
        for n in ast.walk(fn)
    ):
        return False
    for stmt in fn.body:
        if isinstance(stmt, ast.Return):
            if stmt.value is None or not _call_is_oracle(stmt.value, names, modules, binders):
                return False
        elif isinstance(stmt, ast.Try):
            if not _adapter_try_valid(stmt, tree, binders, names, modules):
                return False
        elif not isinstance(stmt, (ast.Assign, ast.AnnAssign)) or not _local_assignment(stmt):
            return False
    return True


def _local_assignment(stmt: ast.Assign | ast.AnnAssign) -> bool:
    targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
    return all(isinstance(target, ast.Name) for target in targets)


def _adapter_try_valid(
    stmt: ast.Try,
    tree: ast.Module,
    binders: dict[str, list[ast.AST]],
    names: set[str],
    modules: dict[str, str],
) -> bool:
    if stmt.orelse or stmt.finalbody:
        return False
    for body_stmt in stmt.body:
        if isinstance(body_stmt, ast.Return):
            if body_stmt.value is None or not _call_is_oracle(
                body_stmt.value, names, modules, binders
            ):
                return False
        elif not isinstance(body_stmt, (ast.Assign, ast.AnnAssign)) or not _local_assignment(
            body_stmt
        ):
            return False
    return all(_adapter_handler_valid(handler, tree, binders) for handler in stmt.handlers)


def _adapter_handler_valid(
    handler: ast.ExceptHandler, tree: ast.Module, binders: dict[str, list[ast.AST]]
) -> bool:
    if not handler.name or not isinstance(handler.type, ast.Name):
        return False
    imported_exception = any(
        isinstance(item, ast.ImportFrom)
        and item.level == 0
        and item.module
        and (item.module == "cairnwork" or item.module.startswith("cairnwork."))
        and any(
            (alias.asname or alias.name) == handler.type.id and alias.name[:1].isupper()
            for alias in item.names
        )
        for item in tree.body
    )
    if not imported_exception or len(binders.get(handler.type.id, [])) != 1:
        return False
    if len(handler.body) != 1 or not isinstance(handler.body[0], ast.Return):
        return False
    value = handler.body[0].value
    return (
        isinstance(value, ast.Attribute)
        and isinstance(value.value, ast.Name)
        and value.value.id == handler.name
        and value.attr == "code"
    )


def _call_is_oracle(
    node: ast.AST,
    names: set[str],
    modules: dict[str, str],
    binders: dict[str, list[ast.AST]],
    adapters: set[str] | None = None,
) -> bool:
    if not isinstance(node, ast.Call):
        return False
    fn = node.func
    if isinstance(fn, ast.Name):
        name = fn.id
    elif isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name):
        name = cast(ast.Name, fn.value).id
    else:
        name = None
    if name is None:
        return False
    if (
        isinstance(fn, ast.Name)
        and adapters
        and name in adapters
        and len(binders.get(name, [])) == 1
    ):
        return True
    allowed = (
        name in names
        if isinstance(fn, ast.Name)
        else ((modules.get(name), cast(Any, fn).attr) in ORACLES)
    )
    if not allowed:
        return False
    return len(binders.get(name, [])) == 1


def _raw_names(
    tree: ast.Module,
    names: set[str],
    modules: dict[str, str],
    binders: dict[str, list[ast.AST]],
    adapters: set[str],
) -> dict[ast.AST, set[str]]:
    safe: dict[ast.AST, set[str]] = {}
    scopes = [
        tree,
        *(n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))),
    ]
    for fn in scopes:
        bindings: dict[str, list[ast.AST]] = {}
        stores: dict[str, int] = {}
        for n in _scope_nodes(fn):
            if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
                stores[n.id] = stores.get(n.id, 0) + 1
            if isinstance(n, ast.arg):
                stores[n.arg] = stores.get(n.arg, 0) + 1
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                stores[n.name] = stores.get(n.name, 0) + 1
            if isinstance(n, (ast.Import, ast.ImportFrom)):
                for alias in n.names:
                    local = alias.asname or (
                        alias.name.split(".")[0] if isinstance(n, ast.Import) else alias.name
                    )
                    stores[local] = stores.get(local, 0) + 1
            if isinstance(n, ast.ExceptHandler) and n.name:
                stores[n.name] = stores.get(n.name, 0) + 1
            if isinstance(n, (ast.MatchAs, ast.MatchStar)) and n.name:
                stores[n.name] = stores.get(n.name, 0) + 1
            if isinstance(n, ast.MatchMapping) and n.rest:
                stores[n.rest] = stores.get(n.rest, 0) + 1
            if isinstance(n, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
                value = n.value
                if value is None:
                    continue
                targets = n.targets if isinstance(n, ast.Assign) else [n.target]
                for target in targets:
                    for name in target_names(target):
                        bindings.setdefault(name, []).append(value)
        good = set()
        for name, values in bindings.items():
            if stores.get(name, 0) != len(values):
                continue
            if all(_call_is_oracle(v, names, modules, binders, adapters) for v in values):
                good.add(name)
            elif all(
                isinstance(v, (ast.Tuple, ast.List))
                and len(v.elts) == 1
                and _call_is_oracle(v.elts[0], names, modules, binders, adapters)
                for v in values
            ):
                good.add(name)
        safe[fn] = good
    return safe


def _scope_nodes(scope: ast.AST) -> Any:
    pending = list(ast.iter_child_nodes(scope))
    while pending:
        node = pending.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            yield node
            continue
        yield node
        pending.extend(ast.iter_child_nodes(node))


def _contains_forbidden(node: ast.AST, names: set[str]) -> bool:
    return any(
        isinstance(n, ast.Call)
        and (
            (isinstance(n.func, ast.Name) and n.func.id in FORBIDDEN)
            or (isinstance(n.func, ast.Attribute) and n.func.attr == "__setattr__")
        )
        for n in ast.walk(node)
    )


def _arg_ok(
    node: ast.AST,
    index: int,
    raw_names: set[str],
    oracle_names: set[str],
    row_names: set[str],
    modules: dict[str, str],
    binders: dict[str, list[ast.AST]],
    adapters: set[str],
) -> bool:
    if index == 0:
        return isinstance(node, ast.Constant) and isinstance(node.value, str)
    if index == 2:
        return literal_string(node)
    if index == 3:
        if _call_is_oracle(node, oracle_names, modules, binders, adapters):
            return not _contains_forbidden(node, oracle_names | row_names | modules.keys())
        if isinstance(node, ast.Name):
            return node.id in raw_names
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            return node.value.id in raw_names and node.attr in {"reason", "code", "status"}
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and isinstance(node.func.value, ast.Name)
        ):
            return (
                node.func.value.id in raw_names
                and len(node.args) == 1
                and not node.keywords
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            )
        return False
    raw_mentions = {n.id for n in ast.walk(node) if isinstance(n, ast.Name) and n.id in raw_names}
    if _contains_forbidden(node, oracle_names | row_names | modules.keys()):
        return False
    for call in (n for n in ast.walk(node) if isinstance(n, ast.Call)):
        if isinstance(call.func, ast.Name) and call.func.id in raw_mentions:
            return False
        if (
            isinstance(call.func, ast.Attribute)
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id in raw_mentions
        ):
            if (
                call.func.attr != "get"
                or len(call.args) != 1
                or not isinstance(call.args[0], ast.Constant)
                or not isinstance(call.args[0].value, str)
            ):
                return False
    return True


def check(source: str, path: str = "witness.py") -> list[int]:
    tree = ast.parse(source, filename=path)
    bindings = _binders(tree)
    oracle_names, modules, row_names, trusted_rows = _module_oracles(tree, bindings, path)
    adapters = _valid_adapters(tree, path, oracle_names, modules, bindings)
    poisoned = {
        name
        for name in oracle_names | modules.keys() | row_names
        if len(bindings.get(name, [])) != 1
    }
    oracle_names -= poisoned
    modules = {name: module for name, module in modules.items() if name not in poisoned}
    row_names -= poisoned
    trusted_rows -= poisoned
    raw_by_fn = _raw_names(tree, oracle_names, modules, bindings, adapters)
    return scan(
        tree, path, bindings, oracle_names, modules, row_names, trusted_rows, adapters, raw_by_fn
    )


from tests._w1353_allow_rules import scan  # noqa: E402
