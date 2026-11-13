"""AST alias tracking and sys/search-path expression detection."""

from __future__ import annotations

import ast
from collections.abc import Sequence
from dataclasses import dataclass, field


@dataclass
class _AliasState:
    sys_aliases: set[str] = field(default_factory=set)
    path_aliases: set[str] = field(default_factory=set)
    sys_funcs: set[str] = field(default_factory=set)
    path_funcs: set[str] = field(default_factory=set)
    env: dict[str, ast.AST] = field(default_factory=dict)


def _dict_lookup_nodes(node: ast.Subscript, env: dict[str, ast.AST] | None) -> Sequence[ast.AST]:
    """Return candidate AST value nodes when indexing an inline or env-bound dict."""
    container = node.value
    if isinstance(container, ast.Name) and env and container.id in env:
        container = env[container.id]
    if not isinstance(container, ast.Dict):
        return []
    if isinstance(node.slice, ast.Constant):
        matched = [
            v
            for k, v in zip(container.keys, container.values, strict=True)
            if isinstance(k, ast.Constant) and k.value == node.slice.value
        ]
        if matched:
            return matched
    return list(container.values)


def is_sys_expr(
    node: ast.AST,
    sys_aliases: set[str],
    sys_funcs: set[str],
    env: dict[str, ast.AST] | None = None,
    depth: int = 0,
) -> bool:
    """Check if an AST expression evaluates to the sys module object."""
    if depth > 8:
        return False
    if isinstance(node, ast.Name):
        return node.id in sys_aliases
    if isinstance(node, ast.Attribute):
        return node.attr == "sys"
    if isinstance(node, ast.Call):
        fn = node.func
        if (isinstance(fn, ast.Attribute) and fn.attr == "import_module") or (
            isinstance(fn, ast.Name) and fn.id == "__import__"
        ):
            return bool(
                node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value == "sys"
            )
        return isinstance(fn, ast.Name) and fn.id in sys_funcs
    if isinstance(node, ast.Subscript):
        if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, int):
            idx = node.slice.value
            if isinstance(node.value, (ast.List, ast.Tuple)) and 0 <= idx < len(node.value.elts):
                return is_sys_expr(node.value.elts[idx], sys_aliases, sys_funcs, env, depth + 1)
        return any(
            is_sys_expr(v, sys_aliases, sys_funcs, env, depth + 1)
            for v in _dict_lookup_nodes(node, env)
        )
    if isinstance(node, ast.IfExp):
        return is_sys_expr(node.body, sys_aliases, sys_funcs, env, depth + 1) or is_sys_expr(
            node.orelse, sys_aliases, sys_funcs, env, depth + 1
        )
    if isinstance(node, ast.BoolOp):
        return any(is_sys_expr(v, sys_aliases, sys_funcs, env, depth + 1) for v in node.values)
    if isinstance(node, ast.NamedExpr):
        return is_sys_expr(node.value, sys_aliases, sys_funcs, env, depth + 1)
    return False


def is_sys_path_expr(
    node: ast.AST,
    sys_aliases: set[str],
    sys_funcs: set[str],
    path_aliases: set[str],
    path_funcs: set[str],
    env: dict[str, ast.AST] | None = None,
    depth: int = 0,
) -> bool:
    """Check if an AST expression evaluates to sys.path or a search path list."""
    if depth > 8:
        return False
    args = (sys_aliases, sys_funcs, path_aliases, path_funcs, env, depth + 1)
    if isinstance(node, ast.Name):
        return node.id in path_aliases or node.id in ("__path__", "submodule_search_locations")
    if isinstance(node, ast.Attribute):
        if node.attr == "path" and is_sys_expr(node.value, sys_aliases, sys_funcs, env, depth + 1):
            return True
        return node.attr in ("__path__", "submodule_search_locations")
    if isinstance(node, ast.Call):
        fn = node.func
        if isinstance(fn, ast.Name) and fn.id == "getattr" and len(node.args) >= 2:
            return is_sys_expr(node.args[0], sys_aliases, sys_funcs, env, depth + 1) and (
                isinstance(node.args[1], ast.Constant) and node.args[1].value == "path"
            )
        return isinstance(fn, ast.Name) and fn.id in path_funcs
    if isinstance(node, ast.Subscript):
        if is_sys_path_expr(node.value, *args):
            return True
        if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, int):
            idx = node.slice.value
            if isinstance(node.value, (ast.List, ast.Tuple)) and 0 <= idx < len(node.value.elts):
                return is_sys_path_expr(node.value.elts[idx], *args)
        return any(is_sys_path_expr(v, *args) for v in _dict_lookup_nodes(node, env))
    if isinstance(node, ast.IfExp):
        return is_sys_path_expr(node.body, *args) or is_sys_path_expr(node.orelse, *args)
    if isinstance(node, ast.BoolOp):
        return any(is_sys_path_expr(v, *args) for v in node.values)
    if isinstance(node, ast.NamedExpr):
        return is_sys_path_expr(node.value, *args)
    return False


def collect_aliases_and_env(
    tree: ast.AST,
) -> tuple[set[str], set[str], set[str], set[str], dict[str, ast.AST]]:
    """Iteratively collect sys/path aliases, helper functions, and constant environment."""
    st = _AliasState()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name == "sys":
                    st.sys_aliases.add(a.asname or "sys")
        elif isinstance(node, ast.ImportFrom) and node.module == "sys":
            for a in node.names:
                if a.name == "path":
                    st.path_aliases.add(a.asname or "path")
                elif a.name == "*":
                    st.path_aliases.add("path")
                else:
                    st.sys_aliases.add(a.asname or a.name)
        elif isinstance(node, ast.ImportFrom) and node.module == "os":
            for a in node.names:
                if a.name == "sys":
                    st.sys_aliases.add(a.asname or "sys")
    for _ in range(8):
        prev = (
            len(st.sys_aliases),
            len(st.path_aliases),
            len(st.sys_funcs),
            len(st.path_funcs),
            len(st.env),
        )
        for node in ast.walk(tree):
            _scan_aliases_in_node(node, st)
        curr = (
            len(st.sys_aliases),
            len(st.path_aliases),
            len(st.sys_funcs),
            len(st.path_funcs),
            len(st.env),
        )
        if curr == prev:
            break
    return st.sys_aliases, st.path_aliases, st.sys_funcs, st.path_funcs, st.env


def _record_alias(name: str, val: ast.AST, st: _AliasState) -> None:
    if is_sys_expr(val, st.sys_aliases, st.sys_funcs, st.env):
        st.sys_aliases.add(name)
    elif is_sys_path_expr(
        val, st.sys_aliases, st.sys_funcs, st.path_aliases, st.path_funcs, st.env
    ):
        st.path_aliases.add(name)


def _scan_aliases_in_node(node: ast.AST, st: _AliasState) -> None:
    if isinstance(node, ast.Assign):
        if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            tid = node.targets[0].id
            st.env[tid] = node.value
            _record_alias(tid, node.value, st)
        elif len(node.targets) == 1 and isinstance(node.targets[0], (ast.Tuple, ast.List)):
            targets = node.targets[0].elts
            if isinstance(node.value, (ast.Tuple, ast.List)) and len(node.value.elts) == len(
                targets
            ):
                for tgt, val in zip(targets, node.value.elts, strict=True):
                    if isinstance(tgt, ast.Name):
                        _record_alias(tgt.id, val, st)
    elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value:
        st.env[node.target.id] = node.value
    elif isinstance(node, ast.NamedExpr) and isinstance(node.target, ast.Name):
        _record_alias(node.target.id, node.value, st)
    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        for arg, default in zip(
            reversed(node.args.args), reversed(node.args.defaults), strict=False
        ):
            if default is not None:
                _record_alias(arg.arg, default, st)
        for bn in ast.walk(node):
            if isinstance(bn, ast.Return) and bn.value is not None:
                if is_sys_expr(bn.value, st.sys_aliases, st.sys_funcs, st.env):
                    st.sys_funcs.add(node.name)
                elif is_sys_path_expr(
                    bn.value, st.sys_aliases, st.sys_funcs, st.path_aliases, st.path_funcs, st.env
                ):
                    st.path_funcs.add(node.name)
