"""Convergent reference-based detection of in-process search-path edits."""

from __future__ import annotations

import ast

from experimental.committed_sys_path.aliases import (
    AliasState,
    PathEdit,
    _is_slice_copy,
    _is_sys_dict,
    collect_aliases_and_env,
    is_sys_expr,
    is_sys_path_expr,
)
from experimental.committed_sys_path.sys_guard import find_sys_and_escape_edits

__all__ = ["PathEdit", "find_path_edits"]


def _extract_new_path_targets(value: ast.AST, st: AliasState) -> list[ast.AST]:
    if isinstance(value, ast.BinOp) and isinstance(value.op, ast.Add):
        return _extract_new_path_targets(value.left, st) + _extract_new_path_targets(
            value.right, st
        )
    if is_sys_path_expr(value, st) or (
        isinstance(value, ast.Name) and value.id in st.slice_copy_aliases
    ):
        return []
    if _is_slice_copy(value, st):
        return []
    if isinstance(value, (ast.List, ast.Tuple)):
        return [elt.value if isinstance(elt, ast.Starred) else elt for elt in value.elts]
    return [value]


def _build_parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    return parents


def _flatten_targets(nodes: list[ast.expr] | tuple[ast.expr, ...]) -> list[ast.expr]:
    flat: list[ast.expr] = []
    for t in nodes:
        if isinstance(t, (ast.Tuple, ast.List)):
            flat.extend(_flatten_targets(t.elts))
        elif isinstance(t, ast.Starred):
            flat.extend(_flatten_targets([t.value]))
        else:
            flat.append(t)
    return flat


def _is_path_target(t: ast.AST, st: AliasState) -> bool:
    if isinstance(t, ast.Name) and (
        t.id in ("__path__", "submodule_search_locations") or t.id in st.path_aliases
    ):
        return True
    return is_sys_path_expr(t, st)


def _mark_handled_target(t: ast.AST, st: AliasState, handled: set[ast.AST]) -> None:
    handled.add(t)
    for sub in ast.walk(t):
        if is_sys_path_expr(sub, st):
            handled.add(sub)


def _mark_handled_concat_sources(val: ast.AST, st: AliasState, handled: set[ast.AST]) -> None:
    if isinstance(val, ast.BinOp) and isinstance(val.op, ast.Add):
        handled.add(val)
        _mark_handled_concat_sources(val.left, st, handled)
        _mark_handled_concat_sources(val.right, st, handled)
    elif is_sys_path_expr(val, st) or _is_slice_copy(val, st):
        handled.add(val)
        if isinstance(val, ast.Subscript):
            handled.add(val.value)
        elif isinstance(val, ast.Call) and isinstance(val.func, ast.Attribute):
            handled.add(val.func.value)


def _is_unhandled_machinery_ref(node: ast.AST, st: AliasState) -> bool:
    if _is_sys_dict(node, st, 0):
        return True
    if isinstance(node, ast.Call):
        fn = node.func
        if isinstance(fn, ast.Name):
            if fn.id in st.helper_funcs or fn.id in st.addsitedir_aliases:
                return True
            if fn.id == "setattr" and len(node.args) >= 2 and is_sys_expr(node.args[0], st):
                if isinstance(node.args[1], ast.Constant) and node.args[1].value == "path":
                    return True
        if isinstance(fn, ast.Attribute) and fn.attr == "addsitedir":
            if isinstance(fn.value, ast.Name) and fn.value.id in st.site_aliases:
                return True
    return False


def _call_targets(attr: str, args: list[ast.expr]) -> list[ast.AST]:
    if attr == "insert" and len(args) >= 2:
        return [args[1]]
    if attr in ("insert", "append") and args:
        return [args[0]]
    if attr == "extend" and args:
        arg = args[0]
        return list(arg.elts) if isinstance(arg, (ast.List, ast.Tuple)) else [arg]
    return []


def _detect_call_edit(
    node: ast.AST, st: AliasState, lines: list[str], edits: list[PathEdit], handled: set[ast.AST]
) -> None:
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
        return
    func, attr, lineno = node.func, node.func.attr, node.lineno
    raw = lines[lineno - 1].strip() if 0 < lineno <= len(lines) else ""
    if attr in ("insert", "append", "extend") and is_sys_path_expr(func.value, st):
        handled.update({node, func, func.value})
        targets = _call_targets(attr, node.args)
        edits.append(PathEdit(lineno, f"sys.path.{attr}", tuple(targets), raw))
    elif (
        attr in ("insert", "append", "extend")
        and isinstance(func.value, ast.Name)
        and func.value.id in st.list_aliases
        and node.args
        and is_sys_path_expr(node.args[0], st)
    ):
        handled.update({node, func, node.args[0]})
        targets = _call_targets(attr, node.args[1:])
        edits.append(PathEdit(lineno, f"sys.path.{attr}", tuple(targets), raw))
    elif (
        attr == "addsitedir"
        and isinstance(func.value, ast.Name)
        and func.value.id in st.site_aliases
    ):
        handled.add(node)
        tgts = tuple(node.args[:1]) or tuple(
            kw.value for kw in node.keywords if kw.arg == "sitedir"
        )
        edits.append(PathEdit(lineno, "site.addsitedir", tgts, raw))
    elif attr in ("syspath_prepend", "syspathinsert"):
        handled.add(node)
        tgts = tuple(node.args[:1]) or tuple(
            kw.value for kw in node.keywords if kw.arg in ("path", "prepend")
        )
        edits.append(PathEdit(lineno, f"pytest.{attr}", tgts, raw))


def _extract_assign_rhs_targets(node: ast.Assign | ast.AugAssign, st: AliasState) -> list[ast.AST]:
    if (
        isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], (ast.Tuple, ast.List))
        and isinstance(node.value, (ast.Tuple, ast.List))
    ):
        lhs_elts = node.targets[0].elts
        rhs_elts = node.value.elts
        no_star = not any(isinstance(e, ast.Starred) for e in (*lhs_elts, *rhs_elts))
        if no_star and len(lhs_elts) == len(rhs_elts):
            picked: list[ast.AST] = []
            for lhs_e, rhs_e in zip(lhs_elts, rhs_elts, strict=True):
                if any(_is_path_target(sub, st) for sub in ast.walk(lhs_e)):
                    picked.extend(_extract_new_path_targets(rhs_e, st))
            return picked
    return _extract_new_path_targets(node.value, st)


def _is_path_alias_binding(node: ast.Assign, st: AliasState) -> bool:
    if len(node.targets) != 1:
        return False
    tgt = node.targets[0]
    if isinstance(tgt, ast.Name):
        return (
            tgt.id not in ("__path__", "submodule_search_locations")
            and tgt.id in st.path_aliases
            and (is_sys_path_expr(node.value, st) or _is_slice_copy(node.value, st))
        )
    if isinstance(tgt, (ast.Tuple, ast.List)) and isinstance(node.value, (ast.Tuple, ast.List)):
        if len(tgt.elts) != len(node.value.elts):
            return False
        for lhs_e, rhs_e in zip(tgt.elts, node.value.elts, strict=True):
            if not isinstance(lhs_e, ast.Name):
                if any(_is_path_target(sub, st) for sub in ast.walk(lhs_e)):
                    return False
                continue
            if lhs_e.id in ("__path__", "submodule_search_locations"):
                return False
            if lhs_e.id in st.path_aliases and not (
                is_sys_path_expr(rhs_e, st) or _is_slice_copy(rhs_e, st)
            ):
                return False
        return True
    return False


def _detect_assign_edit(
    node: ast.AST, st: AliasState, lines: list[str], edits: list[PathEdit], handled: set[ast.AST]
) -> None:
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in st.addsitedir_aliases
    ):
        handled.add(node)
        raw = lines[node.lineno - 1].strip() if 0 < node.lineno <= len(lines) else ""
        tgts = tuple(node.args[:1]) or tuple(
            kw.value for kw in node.keywords if kw.arg == "sitedir"
        )
        edits.append(PathEdit(node.lineno, "site.addsitedir", tgts, raw))
        return
    if not isinstance(node, (ast.Assign, ast.AugAssign)):
        return
    if isinstance(node, ast.Assign) and _is_path_alias_binding(node, st):
        return
    tgts = [node.target] if isinstance(node, ast.AugAssign) else node.targets
    flat_tgts = _flatten_targets(tgts)
    has_path_target = any(_is_path_target(t, st) for t in flat_tgts)
    lineno = node.lineno
    raw = lines[lineno - 1].strip() if 0 < lineno <= len(lines) else ""
    if isinstance(node, ast.Assign) and _is_slice_copy(node.value, st) and not has_path_target:
        _mark_handled_concat_sources(node.value, st, handled)
        return
    if not has_path_target:
        return
    for t in tgts:
        if any(_is_path_target(sub, st) for sub in _flatten_targets([t])):
            _mark_handled_target(t, st, handled)
    targets = _extract_assign_rhs_targets(node, st)
    for tgt in targets:
        handled.add(tgt)
    _mark_handled_concat_sources(node.value, st, handled)
    kind = "sys.path+=" if isinstance(node, ast.AugAssign) else "sys.path="
    edits.append(PathEdit(lineno, kind, tuple(targets), raw))


def _detect_delete_edit(
    node: ast.AST, st: AliasState, lines: list[str], edits: list[PathEdit], handled: set[ast.AST]
) -> None:
    if not isinstance(node, ast.Delete):
        return
    lineno = node.lineno
    raw = lines[lineno - 1].strip() if 0 < lineno <= len(lines) else ""
    for t in node.targets:
        if _is_path_target(t, st):
            _mark_handled_target(t, st, handled)
            edits.append(PathEdit(lineno, "sys.path.del", (), raw))


def find_path_edits(tree: ast.AST, source_text: str) -> list[PathEdit]:
    """Find all in-process search-path and guard edits; fail closed on escapes."""
    st = collect_aliases_and_env(tree)
    parents = _build_parents(tree)
    lines = source_text.splitlines()
    edits: list[PathEdit] = []
    handled_nodes: set[ast.AST] = set()

    for node in ast.walk(tree):
        _detect_call_edit(node, st, lines, edits, handled_nodes)
        _detect_assign_edit(node, st, lines, edits, handled_nodes)
        _detect_delete_edit(node, st, lines, edits, handled_nodes)

    edits.extend(find_sys_and_escape_edits(tree, st, lines, parents, handled_nodes))

    for node in ast.walk(tree):
        if node in handled_nodes:
            continue
        if _is_unhandled_machinery_ref(node, st):
            lineno = getattr(node, "lineno", 0)
            raw = lines[lineno - 1].strip() if 0 < lineno <= len(lines) else ""
            edits.append(PathEdit(lineno, "unresolvable_edit", (), raw))

    return edits
