"""Convergent reference-based detection of in-process search-path edits."""

from __future__ import annotations

import ast
from dataclasses import dataclass

from experimental.committed_sys_path.aliases import (
    AliasState,
    _is_slice_copy,
    _is_sys_dict,
    collect_aliases_and_env,
    is_sys_expr,
    is_sys_path_expr,
)


@dataclass(frozen=True)
class PathEdit:
    """Represents a detected in-process search path edit."""

    lineno: int
    kind: str
    target_nodes: tuple[ast.AST, ...]
    raw_stmt: str


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


def _is_safe_readonly_use(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    parent = parents.get(node)
    if isinstance(parent, ast.Attribute) and parent.value is node:
        gp = parents.get(parent)
        if (
            isinstance(gp, ast.Call)
            and gp.func is parent
            and parent.attr in ("index", "count", "copy")
        ):
            return True
    if (
        isinstance(parent, ast.Subscript)
        and parent.value is node
        and isinstance(parent.ctx, ast.Load)
    ):
        if isinstance(parent.slice, ast.Slice):
            return True
        if isinstance(parent.slice, ast.Constant) and isinstance(parent.slice.value, int):
            return True
    if isinstance(parent, ast.Call) and isinstance(parent.func, ast.Name):
        if parent.func.id in ("len", "iter", "list", "tuple") and node in parent.args:
            return True
    if isinstance(parent, ast.Compare) and all(
        isinstance(op, (ast.In, ast.NotIn)) for op in parent.ops
    ):
        if node in parent.comparators:
            return True
    if isinstance(parent, (ast.For, ast.AsyncFor, ast.comprehension)) and parent.iter is node:
        return True
    return False


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
        edits.append(PathEdit(lineno, "site.addsitedir", tuple(node.args[:1]), raw))


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
        edits.append(PathEdit(node.lineno, "site.addsitedir", tuple(node.args[:1]), raw))
        return
    if not isinstance(node, (ast.Assign, ast.AugAssign)):
        return
    tgts = [node.target] if isinstance(node, ast.AugAssign) else node.targets
    lineno = node.lineno
    raw = lines[lineno - 1].strip() if 0 < lineno <= len(lines) else ""
    if isinstance(node, ast.Assign) and _is_slice_copy(node.value, st):
        handled.add(node.value)
        if isinstance(node.value, ast.Subscript):
            handled.add(node.value.value)
        elif isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Attribute):
            handled.add(node.value.func.value)
        return
    for t in tgts:
        if (
            isinstance(t, ast.Name)
            and t.id not in ("__path__", "submodule_search_locations")
            and t.id not in st.path_aliases
        ):
            continue
        if is_sys_path_expr(t, st):
            handled.add(t)
            if isinstance(t, ast.Subscript):
                handled.add(t.value)
            targets = _extract_new_path_targets(node.value, st)
            for tgt in targets:
                handled.add(tgt)
            if isinstance(node.value, ast.BinOp):
                handled.add(node.value.left)
                handled.add(node.value.right)
            edits.append(
                PathEdit(
                    lineno,
                    "sys.path+=" if isinstance(node, ast.AugAssign) else "sys.path=",
                    tuple(targets),
                    raw,
                )
            )


def find_path_edits(tree: ast.AST, source_text: str) -> list[PathEdit]:
    """Find all in-process search-path edits; fail closed on unrecognized path refs."""
    st = collect_aliases_and_env(tree)
    parents = _build_parents(tree)
    lines = source_text.splitlines()
    edits: list[PathEdit] = []
    handled_nodes: set[ast.AST] = set()

    for node in ast.walk(tree):
        _detect_call_edit(node, st, lines, edits, handled_nodes)
        _detect_assign_edit(node, st, lines, edits, handled_nodes)

    for node in ast.walk(tree):
        if node in handled_nodes:
            continue
        lineno = getattr(node, "lineno", 0)
        raw = lines[lineno - 1].strip() if 0 < lineno <= len(lines) else ""
        if is_sys_path_expr(node, st):
            p = parents.get(node)
            if p in handled_nodes or _is_safe_readonly_use(node, parents):
                continue
            if isinstance(p, (ast.Subscript, ast.Attribute)) and p.value is node:
                if p in handled_nodes:
                    continue
            if isinstance(
                p, (ast.IfExp, ast.BoolOp, ast.NamedExpr, ast.List, ast.Tuple, ast.BinOp)
            ):
                continue
            edits.append(PathEdit(lineno, "unresolvable_edit", (), raw))
        elif _is_unhandled_machinery_ref(node, st):
            edits.append(PathEdit(lineno, "unresolvable_edit", (), raw))

    return edits
