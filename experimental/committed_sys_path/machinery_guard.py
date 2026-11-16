"""Positive read-only rules for sys import machinery and loader/runpy guards."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

from experimental.committed_sys_path.aliases import (
    AliasState,
    PathEdit,
    _non_class_nodes,
    is_sys_expr,
)
from experimental.committed_sys_path.reflection_guard import emit_guard_edit
from experimental.committed_sys_path.sys_guard import (
    MACHINERY_SYS_ATTRS,
    SAFE_PATH_READ_FUNCS,
    is_safe_alias_binding,
    unwrap_transparent_parent,
)

_SAFE_MACHINERY_METHODS = frozenset({"get", "keys", "values", "items", "copy", "index", "count"})
_SAFE_MACHINERY_READ_FUNCS = SAFE_PATH_READ_FUNCS | frozenset({"dict"})
_LOADER_CLASS_NAMES = frozenset(
    {
        "SourceFileLoader",
        "SourcelessFileLoader",
        "ExtensionFileLoader",
        "FileFinder",
        "PathFinder",
    }
)
_UTIL_CALL_NAMES = frozenset({"spec_from_file_location", "module_from_spec"})
_RUNPY_CALL_NAMES = frozenset({"run_path", "run_module"})
_BLOCKED_DIRECT_CALLS = (
    _LOADER_CLASS_NAMES
    | _UTIL_CALL_NAMES
    | _RUNPY_CALL_NAMES
    | frozenset({"reload", "PathFinder.find_spec", "PathFinder.find_module"})
)
_BLOCKED_FUNC_VALUES = (
    _UTIL_CALL_NAMES
    | _RUNPY_CALL_NAMES
    | frozenset(
        {"reload", "FileFinder.path_hook", "PathFinder.find_spec", "PathFinder.find_module"}
    )
)
_EXEC_METHOD_NAMES = frozenset({"exec_module", "load_module", "create_module"})
_IMPORTLIB_LOADER_MODS = frozenset(
    {"importlib.machinery", "importlib._bootstrap", "importlib._bootstrap_external"}
)


@dataclass
class MachineryState:
    """Tracked import-machinery container, module, and loader aliases."""

    machinery_aliases: set[str] = field(default_factory=set)
    importlib_aliases: set[str] = field(default_factory=set)
    util_aliases: set[str] = field(default_factory=set)
    machinery_mod_aliases: set[str] = field(default_factory=set)
    runpy_aliases: set[str] = field(default_factory=set)
    functools_aliases: set[str] = field(default_factory=set)
    partial_aliases: set[str] = field(default_factory=set)
    fn_aliases: dict[str, str] = field(default_factory=dict)


def _record_import(node: ast.Import, mst: MachineryState) -> None:
    for alias in node.names:
        bound = alias.asname or alias.name.split(".")[0]
        if alias.name == "importlib":
            mst.importlib_aliases.add(bound)
        elif alias.name == "importlib.util":
            if alias.asname:
                mst.util_aliases.add(alias.asname)
            else:
                mst.importlib_aliases.add("importlib")
        elif alias.name in _IMPORTLIB_LOADER_MODS:
            if alias.asname:
                mst.machinery_mod_aliases.add(alias.asname)
            elif alias.name.startswith("importlib."):
                mst.importlib_aliases.add("importlib")
        elif alias.name == "runpy":
            mst.runpy_aliases.add(bound)
        elif alias.name == "functools":
            mst.functools_aliases.add(bound)


def _record_import_from(node: ast.ImportFrom, mst: MachineryState) -> None:
    if node.level != 0 or not node.module:
        return
    mod = node.module
    for alias in node.names:
        if alias.name == "*":
            continue
        bound = alias.asname or alias.name
        if mod == "sys" and alias.name in MACHINERY_SYS_ATTRS:
            mst.machinery_aliases.add(bound)
        elif mod == "importlib" and alias.name == "util":
            mst.util_aliases.add(bound)
        elif mod == "importlib" and alias.name == "machinery":
            mst.machinery_mod_aliases.add(bound)
        elif mod == "importlib" and alias.name == "reload":
            mst.fn_aliases[bound] = "reload"
        elif mod == "importlib.util" and alias.name in _UTIL_CALL_NAMES:
            mst.fn_aliases[bound] = alias.name
        elif mod in _IMPORTLIB_LOADER_MODS and alias.name in (
            _LOADER_CLASS_NAMES | _UTIL_CALL_NAMES
        ):
            mst.fn_aliases[bound] = alias.name
        elif mod == "runpy" and alias.name in _RUNPY_CALL_NAMES:
            mst.fn_aliases[bound] = alias.name
        elif mod == "functools" and alias.name == "partial":
            mst.partial_aliases.add(bound)


def _is_mod_expr(node: ast.AST, mst: MachineryState, kind: str) -> bool:
    if isinstance(node, ast.Name):
        sets = {
            "importlib": mst.importlib_aliases,
            "util": mst.util_aliases,
            "machinery": mst.machinery_mod_aliases,
            "runpy": mst.runpy_aliases,
            "functools": mst.functools_aliases,
        }
        return node.id in sets[kind]
    if isinstance(node, ast.Attribute):
        if kind == "util" and node.attr == "util":
            return _is_mod_expr(node.value, mst, "importlib")
        if kind == "machinery" and node.attr in ("machinery", "_bootstrap", "_bootstrap_external"):
            return _is_mod_expr(node.value, mst, "importlib")
    return False


def _is_partial_call(node: ast.Call, mst: MachineryState) -> bool:
    if isinstance(node.func, ast.Name) and node.func.id in mst.partial_aliases:
        return True
    return (
        isinstance(node.func, ast.Attribute)
        and node.func.attr == "partial"
        and _is_mod_expr(node.func.value, mst, "functools")
    )


def _unwrap_container_lookup(node: ast.Subscript) -> list[ast.AST]:
    if isinstance(node.value, (ast.List, ast.Tuple)):
        if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, int):
            idx = node.slice.value
            if -len(node.value.elts) <= idx < len(node.value.elts):
                return [node.value.elts[idx]]
        return list(node.value.elts)
    if isinstance(node.value, ast.Dict):
        return [v for v in node.value.values if v is not None]
    return []


def _resolve_loader_target(node: ast.AST, mst: MachineryState, depth: int = 0) -> str | None:
    if depth > 8:
        return None
    if isinstance(node, ast.Name):
        return mst.fn_aliases.get(node.id)
    if isinstance(node, ast.Attribute):
        if _is_mod_expr(node.value, mst, "importlib") and node.attr == "reload":
            return "reload"
        if _is_mod_expr(node.value, mst, "util") and node.attr in _UTIL_CALL_NAMES:
            return node.attr
        if _is_mod_expr(node.value, mst, "machinery") and node.attr in _LOADER_CLASS_NAMES:
            return node.attr
        if _is_mod_expr(node.value, mst, "runpy") and node.attr in _RUNPY_CALL_NAMES:
            return node.attr
        base = _resolve_loader_target(node.value, mst, depth + 1)
        if base == "FileFinder" and node.attr == "path_hook":
            return "FileFinder.path_hook"
        if base == "PathFinder" and node.attr in ("find_spec", "find_module"):
            return f"PathFinder.{node.attr}"
    if isinstance(node, ast.Call) and _is_partial_call(node, mst) and node.args:
        return _resolve_loader_target(node.args[0], mst, depth + 1)
    if isinstance(node, ast.Subscript):
        for candidate in _unwrap_container_lookup(node):
            hit = _resolve_loader_target(candidate, mst, depth + 1)
            if hit is not None:
                return hit
    return None


def is_machinery_expr(node: ast.AST, st: AliasState, mst: MachineryState, depth: int = 0) -> bool:
    """Return True if node evaluates to sys.meta_path/path_hooks/path_importer_cache/modules."""
    if depth > 8:
        return False
    if isinstance(node, ast.Name):
        return node.id in mst.machinery_aliases
    if isinstance(node, ast.Attribute) and node.attr in MACHINERY_SYS_ATTRS:
        return isinstance(node.ctx, ast.Load) and is_sys_expr(node.value, st, depth + 1)
    if isinstance(node, ast.IfExp):
        return is_machinery_expr(node.body, st, mst, depth + 1) or is_machinery_expr(
            node.orelse, st, mst, depth + 1
        )
    if isinstance(node, ast.BoolOp):
        return any(is_machinery_expr(v, st, mst, depth + 1) for v in node.values)
    if isinstance(node, ast.NamedExpr):
        return is_machinery_expr(node.value, st, mst, depth + 1)
    return False


def _propagate_pair(lhs: ast.AST, rhs: ast.AST, st: AliasState, mst: MachineryState) -> None:
    if isinstance(lhs, ast.Name):
        if is_machinery_expr(rhs, st, mst):
            mst.machinery_aliases.add(lhs.id)
        for kind, target_set in (
            ("importlib", mst.importlib_aliases),
            ("util", mst.util_aliases),
            ("machinery", mst.machinery_mod_aliases),
            ("runpy", mst.runpy_aliases),
            ("functools", mst.functools_aliases),
        ):
            if _is_mod_expr(rhs, mst, kind):
                target_set.add(lhs.id)
        target = _resolve_loader_target(rhs, mst)
        if target is not None:
            mst.fn_aliases[lhs.id] = target
    elif isinstance(lhs, (ast.Tuple, ast.List)) and isinstance(rhs, (ast.Tuple, ast.List)):
        if len(lhs.elts) == len(rhs.elts):
            for l_elem, r_elem in zip(lhs.elts, rhs.elts, strict=True):
                _propagate_pair(l_elem, r_elem, st, mst)


def collect_machinery_state(tree: ast.AST, st: AliasState) -> MachineryState:
    """Collect sys import-machinery aliases and importlib/runpy loader aliases."""
    mst = MachineryState()
    nodes = _non_class_nodes(tree)
    for node in nodes:
        if isinstance(node, ast.Import):
            _record_import(node, mst)
        elif isinstance(node, ast.ImportFrom):
            _record_import_from(node, mst)
    for _ in range(4):
        for node in nodes:
            if isinstance(node, ast.Assign):
                for tgt in node.targets:
                    _propagate_pair(tgt, node.value, st, mst)
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                _propagate_pair(node.target, node.value, st, mst)
            elif isinstance(node, ast.NamedExpr):
                _propagate_pair(node.target, node.value, st, mst)
    return mst


def _is_safe_machinery_read_call(
    top: ast.AST, p: ast.AST | None, parents: dict[ast.AST, ast.AST], st: AliasState
) -> bool:
    if isinstance(p, ast.Starred) and p.value is top:
        gp = parents.get(p)
        return (
            isinstance(gp, ast.Call)
            and isinstance(gp.func, ast.Name)
            and gp.func.id in _SAFE_MACHINERY_READ_FUNCS
            and gp.func.id not in st.rebound_names
            and p in gp.args
        )
    if isinstance(p, ast.Call) and isinstance(p.func, ast.Name):
        has_kw_star = any(kw.arg is None for kw in p.keywords)
        return (
            not has_kw_star
            and p.func.id in _SAFE_MACHINERY_READ_FUNCS
            and p.func.id not in st.rebound_names
            and top in p.args
        )
    return False


def _is_safe_machinery_use(
    node: ast.AST,
    parents: dict[ast.AST, ast.AST],
    st: AliasState,
    mst: MachineryState,
    handled: set[ast.AST],
) -> bool:
    top = unwrap_transparent_parent(node, parents, lambda n: is_machinery_expr(n, st, mst))
    p = parents.get(top)
    if p in handled:
        return True
    if isinstance(p, (ast.Subscript, ast.Attribute)) and p.value is top and p in handled:
        return True
    if isinstance(p, ast.Subscript) and p.value is top and isinstance(p.ctx, ast.Load):
        return True
    if isinstance(p, ast.Attribute) and p.value is top and isinstance(p.ctx, ast.Load):
        gp = parents.get(p)
        return isinstance(gp, ast.Call) and gp.func is p and p.attr in _SAFE_MACHINERY_METHODS
    if _is_safe_machinery_read_call(top, p, parents, st):
        return True
    if isinstance(p, ast.Compare):
        if all(isinstance(op, (ast.Is, ast.IsNot)) for op in p.ops):
            return True
        if all(isinstance(op, (ast.In, ast.NotIn)) for op in p.ops):
            return top in p.comparators
    if isinstance(p, (ast.For, ast.AsyncFor, ast.comprehension)) and p.iter is top:
        return True
    if isinstance(p, (ast.If, ast.While, ast.IfExp)) and p.test is top:
        return True
    if isinstance(p, ast.UnaryOp) and isinstance(p.op, ast.Not) and p.operand is top:
        return True
    return is_safe_alias_binding(
        top, parents, mst.machinery_aliases, lambda n: is_machinery_expr(n, st, mst)
    )


def _is_path_hook_code_probe(call: ast.Call, parents: dict[ast.AST, ast.AST]) -> bool:
    p = parents.get(call)
    return (
        isinstance(p, ast.Attribute)
        and p.value is call
        and p.attr == "__code__"
        and isinstance(p.ctx, ast.Load)
    )


def find_machinery_and_loader_edits(
    tree: ast.AST,
    st: AliasState,
    lines: list[str],
    parents: dict[ast.AST, ast.AST],
    handled: set[ast.AST],
) -> list[PathEdit]:
    """Enforce positive read-only rules on sys machinery and block dynamic loaders."""
    mst = collect_machinery_state(tree, st)
    edits: list[PathEdit] = []

    for node in ast.walk(tree):
        if node in handled:
            continue
        if is_machinery_expr(node, st, mst) and not _is_safe_machinery_use(
            node, parents, st, mst, handled
        ):
            emit_guard_edit(edits, node, "machinery_write", lines, handled)

    for node in ast.walk(tree):
        if node in handled or not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Attribute) and node.func.attr in _EXEC_METHOD_NAMES:
            emit_guard_edit(edits, node, "loader_exec", lines, handled)
            continue
        target = _resolve_loader_target(node.func, mst)
        if target in _BLOCKED_DIRECT_CALLS:
            emit_guard_edit(edits, node, "loader_exec", lines, handled)
            continue
        if target == "FileFinder.path_hook" and not _is_path_hook_code_probe(node, parents):
            emit_guard_edit(edits, node, "loader_exec", lines, handled)
            continue
        if not _is_partial_call(node, mst):
            for arg in list(node.args) + [kw.value for kw in node.keywords]:
                if _resolve_loader_target(arg, mst) in _BLOCKED_FUNC_VALUES:
                    emit_guard_edit(edits, node, "loader_exec", lines, handled)
                    break

    return edits
