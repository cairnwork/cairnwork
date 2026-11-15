"""Scope-aware and import-verified AST alias tracking for committed_sys_path."""

from __future__ import annotations

import ast
import collections
from collections.abc import Sequence
from dataclasses import dataclass, field

_OS_PATH_FNS = frozenset({"dirname", "join", "abspath", "realpath"})
_PATHLIB_CLASSES = frozenset({"Path", "PurePath", "PurePosixPath"})
_MUTATING_PATH_METHODS = frozenset(
    {"insert", "append", "extend", "clear", "pop", "remove", "sort", "reverse"}
)


@dataclass(frozen=True)
class PathEdit:
    """Represents a detected in-process search path or guard edit."""

    lineno: int
    kind: str
    target_nodes: tuple[ast.AST, ...]
    raw_stmt: str


@dataclass
class AliasState:
    """Tracks sys/path/site aliases, unique-binding constants, and verified imports."""

    sys_aliases: set[str] = field(default_factory=set)
    path_aliases: set[str] = field(default_factory=set)
    site_aliases: set[str] = field(default_factory=set)
    addsitedir_aliases: set[str] = field(default_factory=set)
    list_aliases: set[str] = field(default_factory=lambda: {"list"})
    slice_copy_aliases: set[str] = field(default_factory=set)
    sys_funcs: set[str] = field(default_factory=set)
    path_funcs: set[str] = field(default_factory=set)
    helper_funcs: set[str] = field(default_factory=set)
    os_aliases: set[str] = field(default_factory=set)
    ospath_aliases: set[str] = field(default_factory=set)
    path_fn_aliases: dict[str, str] = field(default_factory=dict)
    path_cls_aliases: set[str] = field(default_factory=set)
    pathlib_aliases: set[str] = field(default_factory=set)
    str_trusted: bool = True
    file_rebound: bool = False
    rebound_names: frozenset[str] = frozenset()
    env: dict[str, ast.AST] = field(default_factory=dict)


def _collect_target_names(node: ast.AST, out: list[str]) -> None:
    if isinstance(node, ast.Name):
        out.append(node.id)
    elif isinstance(node, (ast.Tuple, ast.List)):
        for elt in node.elts:
            _collect_target_names(elt, out)
    elif isinstance(node, ast.Starred):
        _collect_target_names(node.value, out)


def _count_bindings(tree: ast.AST) -> collections.Counter[str]:
    """Count all variable bindings across the entire module AST."""
    counts: collections.Counter[str] = collections.Counter()
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                _collect_target_names(t, names)
        elif isinstance(
            node,
            (ast.AnnAssign, ast.AugAssign, ast.NamedExpr, ast.For, ast.AsyncFor, ast.comprehension),
        ):
            _collect_target_names(node.target, names)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if item.optional_vars is not None:
                    _collect_target_names(item.optional_vars, names)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.append(node.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.append(node.name)
            args = node.args
            names.extend(
                a.arg
                for a in (*args.posonlyargs, *args.args, *args.kwonlyargs, args.vararg, args.kwarg)
                if a
            )
        elif isinstance(node, ast.ClassDef):
            names.append(node.name)
        elif isinstance(node, ast.Import):
            names.extend(al.asname or al.name.split(".")[0] for al in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.extend(al.asname or al.name for al in node.names if al.name != "*")
        elif isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name:
            names.append(node.name)
        elif isinstance(node, ast.Delete):
            for t in node.targets:
                _collect_target_names(t, names)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            names.extend(node.names)
    counts.update(names)
    return counts


def _dict_lookup_nodes(node: ast.Subscript, env: dict[str, ast.AST] | None) -> Sequence[ast.AST]:
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


def is_sys_expr(node: ast.AST, st: AliasState, depth: int = 0) -> bool:
    """Check if an AST expression may evaluate to the sys module."""
    if depth > 8:
        return False
    if isinstance(node, ast.Name):
        return node.id in st.sys_aliases
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
        if isinstance(fn, ast.Name) and fn.id == "getattr" and len(node.args) >= 2:
            return isinstance(node.args[1], ast.Constant) and node.args[1].value == "sys"
        return isinstance(fn, ast.Name) and fn.id in st.sys_funcs
    if isinstance(node, ast.Subscript):
        if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, int):
            idx = node.slice.value
            if isinstance(node.value, (ast.List, ast.Tuple)) and 0 <= idx < len(node.value.elts):
                return is_sys_expr(node.value.elts[idx], st, depth + 1)
        return any(is_sys_expr(v, st, depth + 1) for v in _dict_lookup_nodes(node, st.env))
    if isinstance(node, ast.IfExp):
        return is_sys_expr(node.body, st, depth + 1) or is_sys_expr(node.orelse, st, depth + 1)
    if isinstance(node, ast.BoolOp):
        return any(is_sys_expr(v, st, depth + 1) for v in node.values)
    if isinstance(node, ast.NamedExpr):
        return is_sys_expr(node.value, st, depth + 1)
    return False


def _is_sys_dict(node: ast.AST, st: AliasState, depth: int) -> bool:
    if isinstance(node, ast.Attribute) and node.attr == "__dict__":
        return is_sys_expr(node.value, st, depth + 1)
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "vars"
        and node.args
    ):
        return is_sys_expr(node.args[0], st, depth + 1)
    return False


def is_sys_path_expr(node: ast.AST, st: AliasState, depth: int = 0) -> bool:
    """Check if an AST expression may evaluate to sys.path or a search path list."""
    if depth > 8:
        return False
    if isinstance(node, ast.Name):
        return node.id in st.path_aliases or node.id in ("__path__", "submodule_search_locations")
    if isinstance(node, ast.Attribute):
        if node.attr == "path" and is_sys_expr(node.value, st, depth + 1):
            return True
        return node.attr in ("__path__", "submodule_search_locations")
    if isinstance(node, ast.Call):
        fn = node.func
        if isinstance(fn, ast.Name) and fn.id == "getattr" and len(node.args) >= 2:
            return is_sys_expr(node.args[0], st, depth + 1) and (
                isinstance(node.args[1], ast.Constant) and node.args[1].value == "path"
            )
        return isinstance(fn, ast.Name) and fn.id in st.path_funcs
    if isinstance(node, ast.Subscript):
        if _is_sys_dict(node.value, st, depth) and (
            isinstance(node.slice, ast.Constant) and node.slice.value == "path"
        ):
            return True
        if is_sys_path_expr(node.value, st, depth + 1):
            return isinstance(node.ctx, (ast.Store, ast.Del)) or isinstance(node.slice, ast.Slice)
        if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, int):
            idx = node.slice.value
            if isinstance(node.value, (ast.List, ast.Tuple)) and 0 <= idx < len(node.value.elts):
                return is_sys_path_expr(node.value.elts[idx], st, depth + 1)
        return any(is_sys_path_expr(v, st, depth + 1) for v in _dict_lookup_nodes(node, st.env))
    if isinstance(node, ast.IfExp):
        return is_sys_path_expr(node.body, st, depth + 1) or is_sys_path_expr(
            node.orelse, st, depth + 1
        )
    if isinstance(node, ast.BoolOp):
        return any(is_sys_path_expr(v, st, depth + 1) for v in node.values)
    if isinstance(node, ast.NamedExpr):
        return is_sys_path_expr(node.value, st, depth + 1)
    return False


def _is_slice_copy(node: ast.AST, st: AliasState) -> bool:
    if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Slice):
        return is_sys_path_expr(node.value, st)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        return node.func.attr == "copy" and is_sys_path_expr(node.func.value, st)
    return False


def _collect_top_imports(
    tree: ast.Module, counts: collections.Counter[str], st: AliasState
) -> None:
    for stmt in tree.body:
        if isinstance(stmt, ast.Import):
            for a in stmt.names:
                local = a.asname or a.name.split(".")[0]
                if counts[local] != 1:
                    continue
                if a.name == "os":
                    st.os_aliases.add(local)
                elif a.name == "pathlib":
                    st.pathlib_aliases.add(local)
                elif (a.name == "os.path" and a.asname) or a.name == "posixpath":
                    st.ospath_aliases.add(local)
        elif isinstance(stmt, ast.ImportFrom) and stmt.module:
            for a in stmt.names:
                if a.name == "*":
                    continue
                local = a.asname or a.name
                if counts[local] != 1:
                    continue
                if stmt.module in ("os.path", "posixpath") and a.name in _OS_PATH_FNS:
                    st.path_fn_aliases[local] = a.name
                elif stmt.module == "os" and a.name == "path":
                    st.ospath_aliases.add(local)
                elif stmt.module == "os" and a.name == "fspath":
                    st.path_fn_aliases[local] = "fspath"
                elif stmt.module == "pathlib" and a.name in _PATHLIB_CLASSES:
                    st.path_cls_aliases.add(local)


def _record_alias(name: str, val: ast.AST, st: AliasState) -> None:
    if _is_slice_copy(val, st):
        st.slice_copy_aliases.add(name)
        return
    if is_sys_expr(val, st):
        st.sys_aliases.add(name)
    elif is_sys_path_expr(val, st):
        st.path_aliases.add(name)
    elif isinstance(val, ast.Name) and val.id in st.list_aliases:
        st.list_aliases.add(name)


def _is_mutating_path_node(bn: ast.AST, st: AliasState) -> bool:
    if isinstance(bn, ast.Call):
        fn = bn.func
        if isinstance(fn, ast.Attribute):
            if fn.attr == "addsitedir":
                return True
            if fn.attr in _MUTATING_PATH_METHODS and is_sys_path_expr(fn.value, st):
                return True
            if (
                fn.attr in ("insert", "append", "extend")
                and isinstance(fn.value, ast.Name)
                and fn.value.id in st.list_aliases
                and bn.args
                and is_sys_path_expr(bn.args[0], st)
            ):
                return True
        elif isinstance(fn, ast.Name) and (
            fn.id in st.addsitedir_aliases or fn.id in st.helper_funcs
        ):
            return True
    elif isinstance(bn, ast.AugAssign):
        return is_sys_path_expr(bn.target, st)
    elif isinstance(bn, ast.Assign):
        return any(is_sys_path_expr(sub, st) for t in bn.targets for sub in ast.walk(t))
    elif isinstance(bn, ast.Delete):
        return any(is_sys_path_expr(t, st) for t in bn.targets)
    return False


def _non_class_nodes(root: ast.AST) -> list[ast.AST]:
    out: list[ast.AST] = []
    stack: list[tuple[ast.AST, bool]] = [(root, False)]
    while stack:
        node, in_class = stack.pop()
        if not in_class:
            out.append(node)
        if isinstance(node, ast.ClassDef):
            b = {id(s) for s in node.body}
            stack.extend((c, True if id(c) in b else in_class) for c in ast.iter_child_nodes(node))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and in_class:
            b = {id(s) for s in node.body}
            stack.extend((c, id(c) not in b) for c in ast.iter_child_nodes(node))
        else:
            stack.extend((c, in_class) for c in ast.iter_child_nodes(node))
    return out


def _scan_node_aliases(node: ast.AST, st: AliasState) -> None:
    if isinstance(node, ast.Assign):
        if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            _record_alias(node.targets[0].id, node.value, st)
        elif len(node.targets) == 1 and isinstance(node.targets[0], (ast.Tuple, ast.List)):
            targets = node.targets[0].elts
            if isinstance(node.value, (ast.Tuple, ast.List)) and len(node.value.elts) == len(
                targets
            ):
                for tgt, val in zip(targets, node.value.elts, strict=True):
                    if isinstance(tgt, ast.Name):
                        _record_alias(tgt.id, val, st)
    elif (
        isinstance(node, (ast.AnnAssign, ast.NamedExpr))
        and node.value is not None
        and isinstance(node.target, ast.Name)
    ):
        _record_alias(node.target.id, node.value, st)
    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        args = node.args
        all_pos = [*args.posonlyargs, *args.args]
        for arg, default in zip(reversed(all_pos), reversed(args.defaults), strict=False):
            if default is not None:
                _record_alias(arg.arg, default, st)
        has_params = bool(
            args.posonlyargs or args.args or args.kwonlyargs or args.vararg or args.kwarg
        )
        for bn in ast.walk(node):
            if isinstance(bn, ast.Return) and bn.value is not None:
                if is_sys_expr(bn.value, st):
                    st.sys_funcs.add(node.name)
                elif is_sys_path_expr(bn.value, st):
                    st.path_funcs.add(node.name)
            if has_params and _is_mutating_path_node(bn, st):
                st.helper_funcs.add(node.name)


def _alias_sizes(st: AliasState) -> tuple[int, ...]:
    cols = (st.sys_aliases, st.path_aliases, st.sys_funcs, st.path_funcs, st.helper_funcs)
    return (*map(len, cols), len(st.slice_copy_aliases))


def collect_aliases_and_env(tree: ast.AST) -> AliasState:
    """Collect scope-safe unique-binding constants, verified imports, and sys/path aliases."""
    counts = _count_bindings(tree)
    st = AliasState(
        str_trusted=counts["str"] == 0,
        file_rebound=counts["__file__"] > 0,
        rebound_names=frozenset(name for name, cnt in counts.items() if cnt > 0),
    )
    if counts["list"] > 0:
        st.list_aliases.discard("list")
    if isinstance(tree, ast.Module):
        _collect_top_imports(tree, counts, st)
        for stmt in tree.body:
            if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
                tgt, val = stmt.targets[0], stmt.value
            elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
                tgt, val = stmt.target, stmt.value
            else:
                continue
            if isinstance(tgt, ast.Name) and counts[tgt.id] == 1 and tgt.id != "__file__":
                st.env[tgt.id] = val
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name == "sys":
                    st.sys_aliases.add(a.asname or "sys")
                elif a.name == "site":
                    st.site_aliases.add(a.asname or "site")
        elif isinstance(node, ast.ImportFrom) and node.module in ("sys", "os", "site"):
            for a in node.names:
                if node.module == "sys" and a.name in ("path", "*"):
                    st.path_aliases.add(a.asname or "path")
                elif node.module == "os" and a.name == "sys":
                    st.sys_aliases.add(a.asname or "sys")
                elif node.module == "site" and a.name in ("addsitedir", "*"):
                    st.addsitedir_aliases.add(a.asname or "addsitedir")
    scan_nodes = _non_class_nodes(tree)
    for _ in range(8):
        prev = _alias_sizes(st)
        for node in scan_nodes:
            _scan_node_aliases(node, st)
        if _alias_sizes(st) == prev:
            break
    return st
