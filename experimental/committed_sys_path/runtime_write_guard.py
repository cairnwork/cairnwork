"""Guards for runtime .pth, .py, and binary module writes."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field

from experimental.committed_sys_path.aliases import AliasState, PathEdit, _non_class_nodes
from experimental.committed_sys_path.pythonpath_guard import find_pythonpath_edits
from experimental.committed_sys_path.reflection_guard import emit_guard_edit
from experimental.committed_sys_path.scoped_env import (
    ScopedEnvState,
    collect_scoped_env_state,
    is_partial_call,
    lookup_local,
    resolve_str,
    scope_chain,
)

_RUNTIME_SUFFIXES = (".pth", ".py", ".pyc", ".pyo", ".pyd", ".so", ".dylib")
_SAFE_FILE_SUFFIXES = (".txt", ".json", ".yaml", ".yml", ".md", ".log", ".bak", ".tmp", ".csv")
_SHUTIL_WRITE_FUNCS = frozenset({"copy", "copy2", "copyfile", "move"})
_OS_RENAME_FUNCS = frozenset({"rename", "replace", "link", "symlink"})
_PATH_PASSTHROUGH_CALLS = frozenset(
    {"str", "fspath", "Path", "PurePath", "PosixPath", "WindowsPath"}
)
_PATH_PASSTHROUGH_METHODS = frozenset({"resolve", "expanduser", "absolute", "as_posix"})
_PCT_TOKEN_RE = re.compile(
    r"%(?:\([^)]*\))?[#0\- +]*(?:\d+|\*)?(?:\.(?:\d+|\*))?[hlL]?[diouxXeEfFgGcrsa%]"
)
_FMT_TOKEN_RE = re.compile(r"\{[^{}]*\}")


@dataclass
class _FileWriteState:
    builtins_aliases: set[str] = field(default_factory=lambda: {"builtins", "__builtins__"})
    io_aliases: set[str] = field(default_factory=set)
    open_aliases: set[str] = field(default_factory=lambda: {"open"})
    partial_open: dict[str, ast.Call] = field(default_factory=dict)
    shutil_aliases: set[str] = field(default_factory=set)
    shutil_fn_aliases: set[str] = field(default_factory=set)
    os_file_fn_aliases: set[str] = field(default_factory=set)
    bound_path_writers: dict[str, ast.AST] = field(default_factory=dict)
    write_handles: set[tuple[int, str]] = field(default_factory=set)


def _is_open_expr(node: ast.AST, fs: _FileWriteState, depth: int = 0) -> bool:
    if depth > 6:
        return False
    if isinstance(node, ast.Name):
        return node.id in fs.open_aliases
    if isinstance(node, ast.Attribute) and node.attr == "open":
        return isinstance(node.value, ast.Name) and node.value.id in (
            fs.builtins_aliases | fs.io_aliases
        )
    if isinstance(node, ast.Subscript):
        elts: list[ast.AST] = []
        if isinstance(node.value, (ast.List, ast.Tuple)):
            elts = list(node.value.elts)
        elif isinstance(node.value, ast.Dict):
            elts = [v for v in node.value.values if v is not None]
        return any(_is_open_expr(e, fs, depth + 1) for e in elts)
    return False


def _collect_file_write_state(
    tree: ast.AST, ss: ScopedEnvState, parents: dict[ast.AST, ast.AST]
) -> _FileWriteState:
    fs = _FileWriteState()
    mod_id = id(tree)
    for node in _non_class_nodes(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                b = a.asname or a.name.split(".")[0]
                if a.name == "io":
                    fs.io_aliases.add(b)
                elif a.name == "builtins":
                    fs.builtins_aliases.add(b)
                elif a.name == "shutil":
                    fs.shutil_aliases.add(b)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            for a in node.names:
                if a.name == "*":
                    continue
                b = a.asname or a.name
                if node.module in ("builtins", "io") and a.name == "open":
                    fs.open_aliases.add(b)
                elif node.module == "shutil" and a.name in _SHUTIL_WRITE_FUNCS:
                    fs.shutil_fn_aliases.add(b)
                elif node.module == "os" and a.name in _OS_RENAME_FUNCS:
                    fs.os_file_fn_aliases.add(b)
    for _ in range(4):
        for (sid, name), rhss in ss.local_env.items():
            del sid
            for rhs in rhss:
                if _is_open_expr(rhs, fs):
                    fs.open_aliases.add(name)
                elif isinstance(rhs, ast.Call) and is_partial_call(
                    rhs, ss.functools_aliases, ss.partial_aliases
                ):
                    if rhs.args and _is_open_expr(rhs.args[0], fs):
                        fs.partial_open[name] = rhs
                elif isinstance(rhs, ast.Attribute) and rhs.attr in ("write_text", "write_bytes"):
                    fs.bound_path_writers[name] = rhs.value
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in ("write", "writelines") and isinstance(node.func.value, ast.Name):
                for sid in scope_chain(node, parents, mod_id):
                    fs.write_handles.add((sid, node.func.value.id))
    return fs


def _eval_tail_str(
    node: ast.AST,
    scopes: tuple[int, ...],
    ss: ScopedEnvState,
    st: AliasState,
    depth: int = 0,
    visited: frozenset[str] = frozenset(),
    seen: set[int] | None = None,
) -> str | None:
    if depth > 10:
        return None
    if seen is None:
        seen = set()
    nid = id(node)
    if nid in seen:
        return None
    seen.add(nid)
    full = resolve_str(node, scopes, ss.local_env, st, depth + 1)
    if full is not None:
        return full
    if isinstance(node, ast.Name) and node.id not in visited:
        cands = lookup_local(node.id, scopes, ss.local_env)
        next_visited = visited | {node.id}
        last_tail: str | None = None
        for rhs in cands:
            tail = _eval_tail_str(rhs, scopes, ss, st, depth + 1, next_visited, seen)
            if tail and tail.lower().endswith(_RUNTIME_SUFFIXES):
                return tail
            last_tail = tail
        return last_tail if len(cands) == 1 else None
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Div)):
        r_tail = _eval_tail_str(node.right, scopes, ss, st, depth + 1, visited, seen)
        if r_tail is not None:
            l_tail = _eval_tail_str(node.left, scopes, ss, st, depth + 1, visited, seen)
            return r_tail if l_tail is None or isinstance(node.op, ast.Div) else l_tail + r_tail
        return None
    if isinstance(node, ast.JoinedStr) and node.values:
        last = node.values[-1]
        sub = last.value if isinstance(last, ast.FormattedValue) else last
        return _eval_tail_str(sub, scopes, ss, st, depth + 1, visited, seen)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
        fmt = resolve_str(node.left, scopes, ss.local_env, st, depth + 1)
        if fmt is not None:
            matches = list(_PCT_TOKEN_RE.finditer(fmt))
            tail = fmt[matches[-1].end() :] if matches else fmt
            if tail:
                return tail
            if isinstance(node.right, ast.Tuple) and node.right.elts:
                return _eval_tail_str(node.right.elts[-1], scopes, ss, st, depth + 1, visited, seen)
            return _eval_tail_str(node.right, scopes, ss, st, depth + 1, visited, seen)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        attr = node.func.attr
        if attr == "format" and node.args:
            fmt = resolve_str(node.func.value, scopes, ss.local_env, st, depth + 1)
            if fmt is not None:
                matches = list(_FMT_TOKEN_RE.finditer(fmt))
                tail = fmt[matches[-1].end() :] if matches else fmt
                return tail or _eval_tail_str(
                    node.args[-1], scopes, ss, st, depth + 1, visited, seen
                )
        if attr in ("with_suffix", "with_name") and node.args:
            return _eval_tail_str(node.args[0], scopes, ss, st, depth + 1, visited, seen)
        if attr in _PATH_PASSTHROUGH_METHODS or attr == "decode":
            return _eval_tail_str(node.func.value, scopes, ss, st, depth + 1, visited, seen)
        if attr == "join" and len(node.args) == 1:
            seq = node.args[0]
            if isinstance(seq, (ast.List, ast.Tuple)) and seq.elts:
                return _eval_tail_str(seq.elts[-1], scopes, ss, st, depth + 1, visited, seen)
        if attr == "join" and node.args:
            return _eval_tail_str(node.args[-1], scopes, ss, st, depth + 1, visited, seen)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        if node.func.id in _PATH_PASSTHROUGH_CALLS and node.args:
            return _eval_tail_str(node.args[0], scopes, ss, st, depth + 1, visited, seen)
    return None


def _targets_runtime_module(
    node: ast.AST, scopes: tuple[int, ...], ss: ScopedEnvState, st: AliasState
) -> bool:
    tail = _eval_tail_str(node, scopes, ss, st)
    return tail is not None and tail.lower().endswith(_RUNTIME_SUFFIXES)


def _open_call_is_write(
    call: ast.Call,
    mode_node: ast.AST | None,
    scopes: tuple[int, ...],
    fs: _FileWriteState,
    ss: ScopedEnvState,
    st: AliasState,
    parents: dict[ast.AST, ast.AST],
) -> bool:
    if mode_node is not None:
        m = resolve_str(mode_node, scopes, ss.local_env, st)
        return m is None or any(ch in m for ch in "wax+")
    p = parents.get(call)
    if isinstance(p, ast.Attribute) and p.value is call and p.attr in ("write", "writelines"):
        return True
    if (
        isinstance(p, ast.withitem)
        and p.context_expr is call
        and isinstance(p.optional_vars, ast.Name)
    ):
        return any((sid, p.optional_vars.id) in fs.write_handles for sid in scopes)
    if isinstance(p, ast.Assign) and len(p.targets) == 1 and isinstance(p.targets[0], ast.Name):
        return any((sid, p.targets[0].id) in fs.write_handles for sid in scopes)
    return isinstance(p, ast.NamedExpr) and any(
        (sid, p.target.id) in fs.write_handles for sid in scopes
    )


def _unpack_open_call(
    call: ast.Call, fs: _FileWriteState, ss: ScopedEnvState
) -> tuple[bool, ast.AST | None, ast.AST | None]:
    fn = call.func
    if _is_open_expr(fn, fs):
        f_arg = (
            call.args[0]
            if call.args
            else next((k.value for k in call.keywords if k.arg == "file"), None)
        )
        m_arg = (
            call.args[1]
            if len(call.args) >= 2
            else next((k.value for k in call.keywords if k.arg == "mode"), None)
        )
        return True, f_arg, m_arg
    p_call: ast.Call | None = None
    if isinstance(fn, ast.Call) and is_partial_call(fn, ss.functools_aliases, ss.partial_aliases):
        if fn.args and _is_open_expr(fn.args[0], fs):
            p_call = fn
    elif isinstance(fn, ast.Name) and fn.id in fs.partial_open:
        p_call = fs.partial_open[fn.id]
    if p_call is not None:
        combined = list(p_call.args[1:]) + list(call.args)
        kw_map = {k.arg: k.value for k in p_call.keywords if k.arg}
        kw_map.update({k.arg: k.value for k in call.keywords if k.arg})
        f_arg = combined[0] if combined else kw_map.get("file")
        m_arg = combined[1] if len(combined) >= 2 else kw_map.get("mode")
        return True, f_arg, m_arg
    return False, None, None


def _check_file_write_call(
    node: ast.Call,
    scopes: tuple[int, ...],
    fs: _FileWriteState,
    ss: ScopedEnvState,
    st: AliasState,
    parents: dict[ast.AST, ast.AST],
    lines: list[str],
    edits: list[PathEdit],
    handled: set[ast.AST],
) -> None:
    is_open, f_arg, m_arg = _unpack_open_call(node, fs, ss)
    if is_open:
        if f_arg is not None and _open_call_is_write(node, m_arg, scopes, fs, ss, st, parents):
            if _targets_runtime_module(f_arg, scopes, ss, st):
                emit_guard_edit(edits, node, "runtime_file_write", lines, handled)
        return
    fn = node.func
    if isinstance(fn, ast.Attribute):
        if fn.attr in ("write_text", "write_bytes", "symlink_to", "hardlink_to"):
            if _targets_runtime_module(fn.value, scopes, ss, st):
                emit_guard_edit(edits, node, "runtime_file_write", lines, handled)
            return
        if (
            fn.attr in ("rename", "replace")
            and node.args
            and _targets_runtime_module(node.args[0], scopes, ss, st)
        ):
            emit_guard_edit(edits, node, "runtime_file_write", lines, handled)
            return
        if fn.attr == "open" and _targets_runtime_module(fn.value, scopes, ss, st):
            m_arg = (
                node.args[0]
                if node.args
                else next((k.value for k in node.keywords if k.arg == "mode"), None)
            )
            if _open_call_is_write(node, m_arg, scopes, fs, ss, st, parents):
                emit_guard_edit(edits, node, "runtime_file_write", lines, handled)
            return
    if isinstance(fn, ast.Name) and fn.id in fs.bound_path_writers:
        if _targets_runtime_module(fs.bound_path_writers[fn.id], scopes, ss, st):
            emit_guard_edit(edits, node, "runtime_file_write", lines, handled)
            return
    is_copy_or_rename = (
        (
            isinstance(fn, ast.Attribute)
            and fn.attr in _SHUTIL_WRITE_FUNCS
            and isinstance(fn.value, ast.Name)
            and fn.value.id in fs.shutil_aliases
        )
        or (
            isinstance(fn, ast.Attribute)
            and fn.attr in _OS_RENAME_FUNCS
            and isinstance(fn.value, ast.Name)
            and fn.value.id in ss.os_aliases
        )
        or (isinstance(fn, ast.Name) and fn.id in (fs.shutil_fn_aliases | fs.os_file_fn_aliases))
    )
    if is_copy_or_rename:
        dst = (
            node.args[1]
            if len(node.args) >= 2
            else next((k.value for k in node.keywords if k.arg == "dst"), None)
        )
        src = (
            node.args[0]
            if node.args
            else next((k.value for k in node.keywords if k.arg == "src"), None)
        )
        if dst is not None and _targets_runtime_module(dst, scopes, ss, st):
            emit_guard_edit(edits, node, "runtime_file_write", lines, handled)
        elif (
            src is not None
            and dst is not None
            and _targets_runtime_module(src, scopes, ss, st)
            and not (_eval_tail_str(dst, scopes, ss, st) or "")
            .lower()
            .endswith(_SAFE_FILE_SUFFIXES)
        ):
            emit_guard_edit(edits, node, "runtime_file_write", lines, handled)


def find_runtime_write_edits(
    tree: ast.AST,
    st: AliasState,
    lines: list[str],
    parents: dict[ast.AST, ast.AST],
    handled: set[ast.AST],
    *,
    shadow_only: bool = False,
) -> list[PathEdit]:
    """Detect PYTHONPATH environment injections and runtime .py/.pth/binary writes."""
    if shadow_only and not any("pythonpath" in ln.lower() or "putenv" in ln for ln in lines):
        return []
    ss = collect_scoped_env_state(tree, st, parents)
    edits = find_pythonpath_edits(tree, st, ss, lines, parents, handled)
    if shadow_only:
        return edits
    fs = _collect_file_write_state(tree, ss, parents)
    mod_id = id(tree)
    for node in ast.walk(tree):
        if node in handled or not isinstance(node, ast.Call):
            continue
        scopes = scope_chain(node, parents, mod_id)
        _check_file_write_call(node, scopes, fs, ss, st, parents, lines, edits, handled)
    return edits
