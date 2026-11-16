"""Positive-rule validation for sys and sys.path expressions."""

from __future__ import annotations

import ast
from collections.abc import Callable

from experimental.committed_sys_path.aliases import (
    AliasState,
    PathEdit,
    _is_slice_copy,
    is_sys_expr,
    is_sys_path_expr,
)
from experimental.committed_sys_path.reflection_guard import (
    emit_guard_edit,
    find_reflection_edits,
)

_SAFE_WRITABLE_SYS_ATTRS = frozenset(
    {"argv", "dont_write_bytecode", "stdout", "stderr", "ps1", "ps2", "tracebacklimit"}
)
_SAFE_SYS_ATTRS = frozenset(
    {
        "_current_frames",
        "addaudithook",
        "argv",
        "audit",
        "base_exec_prefix",
        "base_prefix",
        "builtin_module_names",
        "byteorder",
        "copyright",
        "cpython",
        "dllhandle",
        "dont_write_bytecode",
        "exc_info",
        "exec_prefix",
        "executable",
        "exit",
        "flags",
        "float_info",
        "getdefaultencoding",
        "getfilesystemencoding",
        "getrecursionlimit",
        "getrefcount",
        "getsizeof",
        "hash_info",
        "hexversion",
        "implementation",
        "int_info",
        "intern",
        "last_exc",
        "last_traceback",
        "last_type",
        "last_value",
        "maxsize",
        "maxunicode",
        "orig_argv",
        "platlibdir",
        "platform",
        "prefix",
        "ps1",
        "ps2",
        "pycache_prefix",
        "settrace",
        "stderr",
        "stdin",
        "stdlib_module_names",
        "stdout",
        "thread_info",
        "version",
        "version_info",
        "warnoptions",
        "winver",
        "__name__",
        "__package__",
        "__spec__",
    }
)
MACHINERY_SYS_ATTRS = frozenset({"meta_path", "path_hooks", "path_importer_cache", "modules"})
SAFE_PATH_READ_FUNCS = frozenset(
    {
        "len",
        "iter",
        "list",
        "tuple",
        "set",
        "frozenset",
        "enumerate",
        "print",
        "bool",
        "str",
        "repr",
        "sorted",
        "reversed",
        "any",
        "all",
        "isinstance",
        "type",
    }
)


def unwrap_transparent_parent(
    node: ast.AST,
    parents: dict[ast.AST, ast.AST],
    check_fn: Callable[[ast.AST], bool],
) -> ast.AST:
    """Walk up transparent conditional, boolean, and walrus expressions."""
    cur = node
    for _ in range(16):
        p = parents.get(cur)
        if isinstance(p, ast.IfExp) and cur in (p.body, p.orelse) and check_fn(p):
            cur = p
        elif isinstance(p, ast.BoolOp) and cur in p.values and check_fn(p):
            cur = p
        elif isinstance(p, ast.NamedExpr) and cur is p.value and check_fn(p):
            cur = p
        else:
            break
    return cur


def _in_class_body(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    cur: ast.AST | None = parents.get(node)
    while cur is not None:
        if isinstance(cur, ast.ClassDef):
            return True
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)):
            return False
        cur = parents.get(cur)
    return False


def _is_matching_rhs(node: ast.AST, st: AliasState, is_path: bool) -> bool:
    if is_path:
        return is_sys_path_expr(node, st) or _is_slice_copy(node, st)
    return is_sys_expr(node, st)


def is_safe_alias_binding(
    node: ast.AST,
    parents: dict[ast.AST, ast.AST],
    allowed_names: set[str],
    match_rhs: Callable[[ast.AST], bool],
) -> bool:
    """Return True if node is solely bound to/from a tracked alias outside a class."""
    p = parents.get(node)
    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
        if node.id not in allowed_names:
            return False
        if isinstance(p, ast.Assign) and len(p.targets) == 1 and p.targets[0] is node:
            return not _in_class_body(p, parents) and match_rhs(p.value)
        if isinstance(p, (ast.Tuple, ast.List)):
            gp = parents.get(p)
            if (
                isinstance(gp, ast.Assign)
                and len(gp.targets) == 1
                and gp.targets[0] is p
                and isinstance(gp.value, (ast.Tuple, ast.List))
                and len(gp.value.elts) == len(p.elts)
            ):
                for lhs_e, rhs_e in zip(p.elts, gp.value.elts, strict=True):
                    if lhs_e is node:
                        return not _in_class_body(gp, parents) and match_rhs(rhs_e)
        if isinstance(p, ast.AnnAssign) and p.target is node:
            return not _in_class_body(p, parents) and (p.value is None or match_rhs(p.value))
        if isinstance(p, ast.NamedExpr) and p.target is node:
            return not _in_class_body(p, parents) and match_rhs(p.value)
        return False

    if isinstance(p, ast.Assign) and len(p.targets) == 1 and p.value is node:
        tgt = p.targets[0]
        if isinstance(tgt, ast.Name) and tgt.id in allowed_names:
            return not _in_class_body(p, parents)
    elif isinstance(p, (ast.Tuple, ast.List)):
        gp = parents.get(p)
        if (
            isinstance(gp, ast.Assign)
            and len(gp.targets) == 1
            and gp.value is p
            and isinstance(gp.targets[0], (ast.Tuple, ast.List))
            and len(gp.targets[0].elts) == len(p.elts)
        ):
            for lhs_e, rhs_e in zip(gp.targets[0].elts, p.elts, strict=True):
                if rhs_e is node and isinstance(lhs_e, ast.Name) and lhs_e.id in allowed_names:
                    return not _in_class_body(gp, parents)
    elif isinstance(p, ast.AnnAssign) and p.value is node and isinstance(p.target, ast.Name):
        if p.target.id in allowed_names:
            return not _in_class_body(p, parents)
    elif isinstance(p, ast.NamedExpr) and p.value is node and p.target.id in allowed_names:
        return not _in_class_body(p, parents)
    return False


def _is_safe_sys_use(
    node: ast.AST,
    parents: dict[ast.AST, ast.AST],
    st: AliasState,
    handled: set[ast.AST],
) -> bool:
    top = unwrap_transparent_parent(node, parents, lambda n: is_sys_expr(n, st))
    p = parents.get(top)
    if isinstance(p, ast.Attribute) and p.value is top:
        if p.attr == "path":
            return isinstance(p.ctx, ast.Load) or p in handled
        if isinstance(p.ctx, ast.Store):
            return p.attr in _SAFE_WRITABLE_SYS_ATTRS
        return isinstance(p.ctx, ast.Load) and p.attr in (
            _SAFE_SYS_ATTRS | _SAFE_WRITABLE_SYS_ATTRS | MACHINERY_SYS_ATTRS
        )
    if isinstance(p, ast.Compare) and all(isinstance(op, (ast.Is, ast.IsNot)) for op in p.ops):
        return True
    return is_safe_alias_binding(top, parents, st.sys_aliases, lambda n: is_sys_expr(n, st))


def is_safe_path_readonly(node: ast.AST, parents: dict[ast.AST, ast.AST], st: AliasState) -> bool:
    """Allow only verified read-only uses of sys.path or search-path aliases."""
    p = parents.get(node)
    if isinstance(p, ast.Attribute) and p.value is node:
        gp = parents.get(p)
        if isinstance(gp, ast.Call) and gp.func is p and p.attr in ("index", "count", "copy"):
            return True
    if isinstance(p, ast.Subscript) and p.value is node and isinstance(p.ctx, ast.Load):
        if isinstance(p.slice, ast.Slice):
            return True
        if isinstance(p.slice, ast.Constant) and isinstance(p.slice.value, int):
            return True
        if (
            isinstance(p.slice, ast.UnaryOp)
            and isinstance(p.slice.op, ast.USub)
            and isinstance(p.slice.operand, ast.Constant)
            and isinstance(p.slice.operand.value, int)
        ):
            return True
    if isinstance(p, ast.Starred) and p.value is node:
        gp = parents.get(p)
        if isinstance(gp, ast.Call) and isinstance(gp.func, ast.Name):
            return (
                gp.func.id in SAFE_PATH_READ_FUNCS
                and gp.func.id not in st.rebound_names
                and p in gp.args
            )
    if isinstance(p, ast.Call) and isinstance(p.func, ast.Name):
        has_kw_star = any(kw.arg is None for kw in p.keywords)
        if (
            not has_kw_star
            and p.func.id in SAFE_PATH_READ_FUNCS
            and p.func.id not in st.rebound_names
            and node in p.args
        ):
            return True
    if isinstance(p, ast.Compare) and all(isinstance(op, (ast.In, ast.NotIn)) for op in p.ops):
        return node in p.comparators
    return isinstance(p, (ast.For, ast.AsyncFor, ast.comprehension)) and p.iter is node


def _is_safe_sys_path_use(
    node: ast.AST,
    parents: dict[ast.AST, ast.AST],
    st: AliasState,
    handled: set[ast.AST],
) -> bool:
    top = unwrap_transparent_parent(node, parents, lambda n: is_sys_path_expr(n, st))
    p = parents.get(top)
    if is_safe_path_readonly(top, parents, st):
        return True
    if p in handled:
        return True
    if isinstance(p, (ast.Subscript, ast.Attribute)) and p.value is top and p in handled:
        return True
    return is_safe_alias_binding(
        top,
        parents,
        st.path_aliases | st.slice_copy_aliases,
        lambda n: _is_matching_rhs(n, st, True),
    )


def find_sys_and_escape_edits(
    tree: ast.AST,
    st: AliasState,
    lines: list[str],
    parents: dict[ast.AST, ast.AST],
    handled: set[ast.AST],
) -> list[PathEdit]:
    """Enforce positive rules on sys/sys.path and block reflection/import escapes."""
    edits = find_reflection_edits(tree, st, lines, parents, handled)
    for node in ast.walk(tree):
        if node in handled:
            continue
        if is_sys_expr(node, st) and not _is_safe_sys_use(node, parents, st, handled):
            emit_guard_edit(edits, node, "sys_escape", lines, handled)
        elif is_sys_path_expr(node, st) and not _is_safe_sys_path_use(node, parents, st, handled):
            emit_guard_edit(edits, node, "sys_path_escape", lines, handled)
    return edits
