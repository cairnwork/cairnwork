"""Positive closed-position checks for values returned by verification oracles."""

from __future__ import annotations

import ast

from tests._w1353_allow_alias import blackbox_unpack as _blackbox_unpack
from tests._w1353_allow_alias import scope_nodes
from tests._w1353_allow_alias import shadowed_call as _shadowed_call

PURE_BUILTINS = {"str", "int", "float", "bool", "len", "repr", "blackbox_result"}
# Nodes that may hold, rebind or hand off a raw value; never allowed between a raw
# expression and the row() argument that contains it.
ESCAPE_NODES = (
    ast.NamedExpr,
    ast.List,
    ast.Tuple,
    ast.Set,
    ast.Dict,
    ast.ListComp,
    ast.SetComp,
    ast.DictComp,
    ast.GeneratorExp,
    ast.Starred,
    ast.Await,
    ast.Yield,
    ast.YieldFrom,
    ast.Call,
)


def _scope_bindings(scope: ast.AST) -> dict[str, list[ast.AST]]:
    found: dict[str, list[ast.AST]] = {}
    for node in scope_nodes(scope):
        names = set()
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
        if isinstance(node, ast.arg):
            names.add(node.arg)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update(
                item.asname
                or (item.name.split(".")[0] if isinstance(node, ast.Import) else item.name)
                for item in node.names
            )
        if isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        if isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name:
            names.add(node.name)
        if isinstance(node, ast.MatchMapping) and node.rest:
            names.add(node.rest)
        for name in names:
            found.setdefault(name, []).append(node)
    return found


def _computed_raw(node: ast.AST, raw: set[str], oracle) -> bool:
    """A value computed at an expression sink stays raw, so it must reach a sink too."""
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Name):
            return (
                func.id in PURE_BUILTINS
                and len(node.args) == 1
                and not node.keywords
                and _raw_expr(node.args[0], raw, oracle)
            )
        return (
            isinstance(func, ast.Attribute)
            and func.attr == "startswith"
            and len(node.args) == 1
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
            and not node.keywords
            and _raw_expr(func.value, raw, oracle)
        )
    if isinstance(node, ast.Compare):
        return any(_raw_expr(item, raw, oracle) for item in (node.left, *node.comparators))
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return _raw_expr(node.operand, raw, oracle)
    if isinstance(node, ast.FormattedValue):
        return _raw_expr(node.value, raw, oracle)
    if isinstance(node, ast.JoinedStr):
        return any(_raw_expr(item, raw, oracle) for item in node.values)
    return False


def _raw_expr(node: ast.AST, raw: set[str], oracle) -> bool:
    if isinstance(node, ast.Name):
        return isinstance(node.ctx, ast.Load) and node.id in raw
    if _computed_raw(node, raw, oracle):
        return True
    if isinstance(node, ast.Call):
        if oracle(node):
            return True
        default_ok = (
            len(node.args) < 2
            or not _raw_mention(node.args[1], raw, oracle)
            or _raw_expr(node.args[1], raw, oracle)
        )
        return (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and _raw_expr(node.func.value, raw, oracle)
            and len(node.args) in {1, 2}
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
            and not node.keywords
            and default_ok
        )
    if isinstance(node, ast.Attribute):
        return isinstance(node.ctx, ast.Load) and _raw_expr(node.value, raw, oracle)
    if isinstance(node, ast.Subscript):
        return (
            isinstance(node.ctx, ast.Load)
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, (str, int))
            and _raw_expr(node.value, raw, oracle)
        )
    if isinstance(node, ast.BoolOp):
        return any(_raw_expr(value, raw, oracle) for value in node.values)
    if isinstance(node, ast.IfExp):
        return _raw_expr(node.body, raw, oracle) or _raw_expr(node.orelse, raw, oracle)
    return False


def _raw_mention(node: ast.AST, raw: set[str], oracle) -> bool:
    return any(
        isinstance(item, ast.Name)
        and isinstance(item.ctx, ast.Load)
        and item.id in raw
        or isinstance(item, ast.Call)
        and oracle(item)
        for item in ast.walk(node)
    )


def _is_single_name_assign(node: ast.AST) -> ast.Name | None:
    if (
        isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
    ):
        return node.targets[0]
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return node.target
    return None


def _single_yield_target(node: ast.AST) -> ast.Name | None:
    """Read one item from the judge's generator without creating an alias container."""
    if not isinstance(node, ast.Assign) or len(node.targets) != 1:
        return None
    target = node.targets[0]
    return (
        target.elts[0]
        if isinstance(target, ast.Tuple)
        and len(target.elts) == 1
        and isinstance(target.elts[0], ast.Name)
        else None
    )


def _collect_raw(
    scope: ast.AST, oracle, adapters: set[str]
) -> tuple[set[str], set[str], dict[str, list[ast.AST]]]:
    bindings = _scope_bindings(scope)
    statements = list(scope_nodes(scope))
    candidates: dict[str, list[ast.AST]] = {}
    seeds = set()
    for node in statements:
        target = _is_single_name_assign(node)
        value = node.value if isinstance(node, (ast.Assign, ast.AnnAssign)) else None
        target = target or _single_yield_target(node)
        if target is None or value is None:
            continue
        if isinstance(value, ast.Call) and oracle(value):
            candidates.setdefault(target.id, []).append(value)
            if len(bindings.get(target.id, [])) == 1:
                seeds.add(target.id)
    derived = set()
    changed = True
    while changed:
        changed = False
        for node in statements:
            target = _is_single_name_assign(node)
            value = node.value if isinstance(node, (ast.Assign, ast.AnnAssign)) else None
            target = target or _single_yield_target(node)
            if target is None or value is None or target.id in derived or target.id in seeds:
                continue
            if _raw_expr(value, seeds | derived, oracle):
                candidates.setdefault(target.id, []).append(value)
                if len(bindings.get(target.id, [])) == 1:
                    derived.add(target.id)
                    changed = True
    return seeds | derived, seeds | derived, bindings


def _allowed_parent(
    node: ast.AST,
    parent: ast.AST | None,
    tree: ast.Module,
    row_names,
    adapters,
    oracle,
    raw,
    bindings,
    global_bindings,
) -> bool:
    if parent is None:
        return False
    if isinstance(parent, ast.Call):
        if node in parent.args:
            if isinstance(parent.func, ast.Name) and parent.func.id in row_names:
                return True
            if (
                isinstance(parent.func, ast.Name)
                and parent.func.id in PURE_BUILTINS
                and (
                    parent.func.id == "blackbox_result"
                    or parent.func.id not in global_bindings
                    and parent.func.id not in bindings
                )
                and len(parent.args) == 1
                and not parent.keywords
            ):
                return True
        return False
    if isinstance(parent, ast.Compare):
        return node is parent.left or node in parent.comparators
    if isinstance(parent, (ast.If, ast.While, ast.Assert)):
        return node is parent.test
    if isinstance(parent, ast.IfExp):
        return node is parent.test
    if isinstance(parent, ast.UnaryOp) and isinstance(parent.op, ast.Not):
        return node is parent.operand
    if isinstance(parent, ast.FormattedValue):
        return node is parent.value
    if isinstance(parent, ast.Expr):
        return _computed_raw(node, raw, oracle)
    if _blackbox_unpack(node, parent, bindings):
        return True
    if isinstance(parent, (ast.Assign, ast.AnnAssign)) and parent.value is node:
        target = _is_single_name_assign(parent)
        if target is None:
            target = _single_yield_target(parent)
            if target is not None and isinstance(node, ast.Call) and oracle(node):
                return len(bindings.get(target.id, [])) == 1
        return target is not None and len(bindings.get(target.id, [])) == 1
    if isinstance(parent, ast.Return) and parent.value is node:
        fn = next(
            (
                fn
                for fn in ast.walk(tree)
                if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and node in ast.walk(fn)
            ),
            None,
        )
        return fn is not None and fn.name in adapters
    return False


def _inside_row_argument(node, parents, row_names, raw, oracle, modules, binders, adapters):
    from tests._w1353_allow import _arg_ok

    current = node
    parent = parents.get(current)
    while parent is not None:
        if (
            isinstance(parent, ast.Call)
            and isinstance(parent.func, ast.Name)
            and parent.func.id in row_names
        ):
            if current in parent.args:
                index = parent.args.index(current)
                if any(isinstance(step, ESCAPE_NODES) for step in _path(node, current, parents)):
                    return False
                return _arg_ok(current, index, raw, set(), row_names, modules, binders, adapters)
        if isinstance(parent, (ast.stmt, ast.comprehension, ast.Lambda)):
            return False
        current, parent = parent, parents.get(parent)
    return False


def _path(node, top, parents):
    current = node
    while current is not top:
        current = parents[current]
        if current is not top:
            yield current


def _startswith_call(node: ast.AST, parent: ast.AST | None) -> bool:
    """`<raw>.startswith("<str>")` yields a bool and cannot change the raw value."""
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "startswith"
        and isinstance(parent, ast.Call)
        and parent.func is node
        and len(parent.args) == 1
        and isinstance(parent.args[0], ast.Constant)
        and isinstance(parent.args[0].value, str)
        and not parent.keywords
    )


def position_findings(tree, oracle_names, modules, binders, adapters, row_names):
    """Return raw-flow findings and the raw names used by the legacy row argument pins."""
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    scopes = [
        tree,
        *(
            item
            for item in ast.walk(tree)
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        ),
    ]
    all_raw: dict[ast.AST, set[str]] = {}
    scope_info = {}
    findings = set()

    def is_oracle(node):
        from tests._w1353_allow import _call_is_oracle

        return _call_is_oracle(node, oracle_names, modules, binders, adapters)

    for scope in scopes:
        raw, _, local_bindings = _collect_raw(scope, is_oracle, adapters)
        all_raw[scope] = raw
        scope_info[scope] = (raw, local_bindings)
        # A candidate name with a raw assignment but multiple bindings is poisoned.
        for name, nodes in _scope_bindings(scope).items():
            if len(nodes) > 1 and any(
                (target := _is_single_name_assign(stmt)) is not None
                and target.id == name
                and stmt.value is not None
                and _raw_expr(stmt.value, raw, is_oracle)
                for stmt in scope_nodes(scope)
                if isinstance(stmt, (ast.Assign, ast.AnnAssign))
            ):
                findings.add(getattr(nodes[1], "lineno", 1))

    for scope in scopes:
        raw, local_bindings = scope_info[scope]
        scoped = set(scope_nodes(scope)) | {scope}
        for node in ast.walk(scope):
            if node not in scoped and node is not scope:
                continue
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                and node is not scope
            ):
                continue
            if not _raw_expr(node, raw, is_oracle):
                continue
            parent = parents.get(node)
            if _shadowed_call(parent, tree, PURE_BUILTINS, binders, local_bindings):
                findings.add(getattr(node, "lineno", 1))
                continue
            parent_wraps = isinstance(parent, ast.expr) and _raw_expr(parent, raw, is_oracle)
            if isinstance(parent, ast.Call) and is_oracle(parent):
                parent_wraps = False
            if parent_wraps or _startswith_call(node, parent):
                continue
            if not _allowed_parent(
                node,
                parent,
                tree,
                row_names,
                adapters,
                is_oracle,
                raw,
                local_bindings,
                binders,
            ) and not _inside_row_argument(
                node, parents, row_names, raw, is_oracle, {}, binders, adapters
            ):
                findings.add(getattr(node, "lineno", 1))
    return sorted(findings), all_raw
