"""Guards for PYTHONPATH environment injections across os.environ and subprocesses."""

from __future__ import annotations

import ast

from experimental.committed_sys_path.aliases import AliasState, PathEdit
from experimental.committed_sys_path.reflection_guard import emit_guard_edit
from experimental.committed_sys_path.scoped_env import (
    ScopedEnvState,
    is_environ_expr,
    lookup_local,
    resolve_str,
    scope_chain,
    subprocess_env_arg,
)


def _is_safe_pythonpath_read(
    node: ast.AST, scopes: tuple[int, ...], ss: ScopedEnvState, st: AliasState
) -> bool:
    if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
        return all(
            _is_safe_pythonpath_read(v, scopes, ss, st)
            or resolve_str(v, scopes, ss.local_env, st) == ""
            for v in node.values
        )
    if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load):
        key = resolve_str(node.slice, scopes, ss.local_env, st)
        return key is not None and key.upper() == "PYTHONPATH"
    if isinstance(node, ast.Call):
        is_get = isinstance(node.func, ast.Attribute) and node.func.attr == "get"
        is_getenv = (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "getenv"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in ss.os_aliases
        ) or (isinstance(node.func, ast.Name) and node.func.id in ss.getenv_aliases)
        if not (is_get or is_getenv):
            return False
        key_node = (
            node.args[0]
            if node.args
            else next((k.value for k in node.keywords if k.arg == "key"), None)
        )
        if key_node is None:
            return False
        key = resolve_str(key_node, scopes, ss.local_env, st)
        if key is None or key.upper() != "PYTHONPATH":
            return False
        dflt = (
            node.args[1]
            if len(node.args) >= 2
            else next((k.value for k in node.keywords if k.arg == "default"), None)
        )
        return dflt is None or (isinstance(dflt, ast.Constant) and dflt.value in (None, ""))
    return False


def _is_pathsep(node: ast.AST, scopes: tuple[int, ...], ss: ScopedEnvState, st: AliasState) -> bool:
    if isinstance(node, ast.Attribute) and node.attr == "pathsep":
        return isinstance(node.value, ast.Name) and node.value.id in ss.os_aliases
    return resolve_str(node, scopes, ss.local_env, st) in (":", ";")


def _extract_pythonpath_nodes(
    val: ast.AST,
    scopes: tuple[int, ...],
    ss: ScopedEnvState,
    st: AliasState,
    depth: int = 0,
    seen: frozenset[int] = frozenset(),
) -> list[ast.AST] | None:
    if depth > 8:
        return None
    nid = id(val)
    if nid in seen:
        return None
    next_seen = seen | {nid}
    if resolve_str(val, scopes, ss.local_env, st) == "" or _is_safe_pythonpath_read(
        val, scopes, ss, st
    ):
        return []
    if isinstance(val, ast.IfExp):
        left = _extract_pythonpath_nodes(val.body, scopes, ss, st, depth + 1, next_seen)
        right = _extract_pythonpath_nodes(val.orelse, scopes, ss, st, depth + 1, next_seen)
        return None if left is None or right is None else left + right
    if isinstance(val, ast.Name) and val.id not in st.env:
        cands = lookup_local(val.id, scopes, ss.local_env)
        if len(cands) == 1:
            return _extract_pythonpath_nodes(cands[0], scopes, ss, st, depth + 1, next_seen)
    if (
        isinstance(val, ast.Call)
        and isinstance(val.func, ast.Attribute)
        and val.func.attr == "join"
    ):
        if not _is_pathsep(val.func.value, scopes, ss, st):
            return [val]
        if len(val.args) != 1 or val.keywords:
            return None
        seq, filter_none = val.args[0], False
        if (
            isinstance(seq, ast.Call)
            and isinstance(seq.func, ast.Name)
            and seq.func.id == "filter"
            and seq.func.id not in st.rebound_names
            and len(seq.args) == 2
            and not seq.keywords
        ):
            pred = seq.args[0]
            if (isinstance(pred, ast.Constant) and pred.value is None) or (
                isinstance(pred, ast.Name) and pred.id == "bool" and "bool" not in st.rebound_names
            ):
                filter_none = True
                seq = seq.args[1]
        if isinstance(seq, ast.Name) and seq.id not in st.env:
            cands = lookup_local(seq.id, scopes, ss.local_env)
            if len(cands) == 1:
                seq = cands[0]
        if not isinstance(seq, (ast.List, ast.Tuple)) or any(
            isinstance(e, ast.Starred) for e in seq.elts
        ):
            return None
        return [
            elem
            for elem in seq.elts
            if not (filter_none and resolve_str(elem, scopes, ss.local_env, st) == "")
            and not _is_safe_pythonpath_read(elem, scopes, ss, st)
        ]
    return [val]


def _emit_pythonpath(
    anchor: ast.AST,
    val_node: ast.AST | None,
    scopes: tuple[int, ...],
    ss: ScopedEnvState,
    st: AliasState,
    lines: list[str],
    edits: list[PathEdit],
    handled: set[ast.AST],
) -> None:
    if anchor in handled:
        return
    if val_node is None:
        emit_guard_edit(edits, anchor, "pythonpath_edit", lines, handled)
        return
    targets = _extract_pythonpath_nodes(val_node, scopes, ss, st)
    if targets == []:
        handled.add(anchor)
        return
    handled.add(anchor)
    lineno = getattr(anchor, "lineno", 0)
    raw = lines[lineno - 1].strip() if 0 < lineno <= len(lines) else ""
    edits.append(
        PathEdit(lineno, "pythonpath_edit", () if targets is None else tuple(targets), raw)
    )


def _inspect_env_mapping(
    expr: ast.AST,
    anchor: ast.AST,
    scopes: tuple[int, ...],
    ss: ScopedEnvState,
    st: AliasState,
    lines: list[str],
    edits: list[PathEdit],
    handled: set[ast.AST],
    depth: int = 0,
    seen: set[int] | None = None,
) -> None:
    if depth > 6:
        return
    if seen is None:
        seen = set()
    nid = id(expr)
    if nid in seen:
        return
    seen.add(nid)
    if isinstance(expr, ast.Dict):
        for k, v in zip(expr.keys, expr.values, strict=True):
            if k is None:
                _inspect_env_mapping(
                    v, anchor, scopes, ss, st, lines, edits, handled, depth + 1, seen
                )
            else:
                kstr = resolve_str(k, scopes, ss.local_env, st)
                if kstr is not None and kstr.upper() == "PYTHONPATH":
                    _emit_pythonpath(k, v, scopes, ss, st, lines, edits, handled)
    elif isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.BitOr):
        _inspect_env_mapping(
            expr.left, anchor, scopes, ss, st, lines, edits, handled, depth + 1, seen
        )
        _inspect_env_mapping(
            expr.right, anchor, scopes, ss, st, lines, edits, handled, depth + 1, seen
        )
    elif isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name) and expr.func.id == "dict":
        for kw in expr.keywords:
            if kw.arg and kw.arg.upper() == "PYTHONPATH":
                _emit_pythonpath(kw, kw.value, scopes, ss, st, lines, edits, handled)
            elif kw.arg is None:
                _inspect_env_mapping(
                    kw.value, anchor, scopes, ss, st, lines, edits, handled, depth + 1, seen
                )
        if expr.args and isinstance(expr.args[0], (ast.List, ast.Tuple)):
            for pair in expr.args[0].elts:
                if isinstance(pair, (ast.List, ast.Tuple)) and len(pair.elts) == 2:
                    kstr = resolve_str(pair.elts[0], scopes, ss.local_env, st)
                    if kstr is not None and kstr.upper() == "PYTHONPATH":
                        _emit_pythonpath(pair, pair.elts[1], scopes, ss, st, lines, edits, handled)
    elif isinstance(expr, ast.Name):
        for rhs in lookup_local(expr.id, scopes, ss.local_env):
            _inspect_env_mapping(
                rhs, anchor, scopes, ss, st, lines, edits, handled, depth + 1, seen
            )


def _is_tracked_env(obj: ast.AST, scopes: tuple[int, ...], ss: ScopedEnvState) -> bool:
    if is_environ_expr(obj, ss):
        return True
    return isinstance(obj, ast.Name) and any((sid, obj.id) in ss.env_var_names for sid in scopes)


def find_pythonpath_edits(
    tree: ast.AST,
    st: AliasState,
    ss: ScopedEnvState,
    lines: list[str],
    parents: dict[ast.AST, ast.AST],
    handled: set[ast.AST],
) -> list[PathEdit]:
    """Detect PYTHONPATH environment injections across os.environ, putenv, and subprocess."""
    mod_id = id(tree)
    edits: list[PathEdit] = []
    for node in ast.walk(tree):
        if node in handled or not isinstance(node, (ast.Assign, ast.AugAssign, ast.Call)):
            continue
        scopes = scope_chain(node, parents, mod_id)
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            tgt = node.targets[0]
            if isinstance(tgt, ast.Subscript) and _is_tracked_env(tgt.value, scopes, ss):
                kstr = resolve_str(tgt.slice, scopes, ss.local_env, st)
                if kstr is not None and kstr.upper() == "PYTHONPATH":
                    _emit_pythonpath(node, node.value, scopes, ss, st, lines, edits, handled)
        elif (
            isinstance(node, ast.AugAssign)
            and isinstance(node.op, ast.BitOr)
            and _is_tracked_env(node.target, scopes, ss)
        ):
            _inspect_env_mapping(node.value, node, scopes, ss, st, lines, edits, handled)
        elif isinstance(node, ast.Call):
            fn = node.func
            is_putenv = (
                isinstance(fn, ast.Attribute)
                and fn.attr == "putenv"
                and isinstance(fn.value, ast.Name)
                and fn.value.id in ss.os_aliases
            ) or (isinstance(fn, ast.Name) and fn.id in ss.putenv_aliases)
            if is_putenv and len(node.args) >= 2:
                kstr = resolve_str(node.args[0], scopes, ss.local_env, st)
                if kstr is None or kstr.upper() == "PYTHONPATH":
                    _emit_pythonpath(node, node.args[1], scopes, ss, st, lines, edits, handled)
            elif isinstance(fn, ast.Attribute) and _is_tracked_env(fn.value, scopes, ss):
                if fn.attr == "update":
                    for arg in node.args:
                        _inspect_env_mapping(arg, node, scopes, ss, st, lines, edits, handled)
                    for kw in node.keywords:
                        if kw.arg and kw.arg.upper() == "PYTHONPATH":
                            _emit_pythonpath(kw, kw.value, scopes, ss, st, lines, edits, handled)
                        elif kw.arg is None:
                            _inspect_env_mapping(
                                kw.value, node, scopes, ss, st, lines, edits, handled
                            )
                elif fn.attr == "setdefault" and node.args:
                    kstr = resolve_str(node.args[0], scopes, ss.local_env, st)
                    if kstr is not None and kstr.upper() == "PYTHONPATH":
                        val = node.args[1] if len(node.args) >= 2 else ast.Constant("")
                        _emit_pythonpath(node, val, scopes, ss, st, lines, edits, handled)
            env_arg = subprocess_env_arg(node, ss)
            if env_arg is not None:
                _inspect_env_mapping(env_arg, node, scopes, ss, st, lines, edits, handled)
    return edits
