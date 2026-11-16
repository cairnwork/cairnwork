"""Reflection, scope-dict, and dynamic import escape blocker."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

from experimental.committed_sys_path.aliases import (
    AliasState,
    PathEdit,
    is_sys_expr,
    is_sys_path_expr,
)

_SENSITIVE_MODULES = frozenset({"sys", "os", "builtins", "importlib", "site", "runpy"})
_DICT_ACCESS_MODS = frozenset(
    {"sys", "os", "builtins", "__builtins__", "importlib", "site", "runpy"}
)
_SENSITIVE_ATTRS = frozenset(
    "sys path meta_path path_hooks path_importer_cache modules environ open exec eval compile "
    "getattr setattr delattr __getattribute__ __setattr__ __import__ import_module reload "
    "__dict__ __builtins__ __globals__ addsitedir insert append extend syspath_prepend "
    "syspathinsert spec_from_file_location module_from_spec exec_module load_module "
    "load_source run_path run_module write_text write_bytes "
    "SourceFileLoader SourcelessFileLoader ExtensionFileLoader FileFinder PathFinder "
    "create_module path_hook".split()
)
_BUILTIN_GUARD_CALLS = frozenset(
    "__import__ import_module getattr setattr delattr globals locals vars "
    "eval exec compile attrgetter methodcaller".split()
)
_CONTAINER_TRAP_NAMES = frozenset(
    {"__import__", "globals", "locals", "eval", "exec", "getattr", "setattr"}
)
_UNTRACKED_SENSITIVE_ATTRS = frozenset(
    "sys __import__ import_module addsitedir syspath_prepend syspathinsert "
    "__builtins__ __globals__ __getattribute__ __setattr__ eval exec".split()
)


@dataclass
class EscapeAliases:
    """Aliases to reflection, builtins, and import helpers within one module."""

    builtins_aliases: set[str] = field(default_factory=lambda: {"builtins", "__builtins__"})
    importlib_aliases: set[str] = field(default_factory=set)
    operator_aliases: set[str] = field(default_factory=set)
    runpy_aliases: set[str] = field(default_factory=set)
    os_aliases: set[str] = field(default_factory=set)
    modules_aliases: set[str] = field(default_factory=set)
    dyn_mod_vars: set[str] = field(default_factory=set)
    fn_aliases: dict[str, str] = field(default_factory=dict)


def emit_guard_edit(
    edits: list[PathEdit], node: ast.AST, kind: str, lines: list[str], handled: set[ast.AST]
) -> None:
    """Record a guard PathEdit once for an offending AST node."""
    if node in handled:
        return
    handled.add(node)
    lineno = getattr(node, "lineno", 0)
    raw = lines[lineno - 1].strip() if 0 < lineno <= len(lines) else ""
    edits.append(PathEdit(lineno, kind, (), raw))


def const_str(node: ast.AST, st: AliasState, depth: int = 0) -> str | None:
    """Evaluate simple constant strings, unique bindings, concatenations, and f-strings."""
    if depth > 8:
        return None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name) and node.id in st.env:
        return const_str(st.env[node.id], st, depth + 1)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = const_str(node.left, st, depth + 1)
        right = const_str(node.right, st, depth + 1)
        return None if left is None or right is None else left + right
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for v in node.values:
            sub = v.value if isinstance(v, ast.FormattedValue) else v
            piece = const_str(sub, st, depth + 1)
            if piece is None:
                return None
            parts.append(piece)
        return "".join(parts)
    return None


def _is_sensitive_module_name(name: str) -> bool:
    return any(name == m or name.startswith(m + ".") for m in _SENSITIVE_MODULES)


def _resolve_escape_fn(val: ast.AST, ea: EscapeAliases) -> str | None:
    if isinstance(val, ast.Name):
        return val.id if val.id in _BUILTIN_GUARD_CALLS else ea.fn_aliases.get(val.id)
    if isinstance(val, ast.Attribute) and isinstance(val.value, ast.Name):
        base = val.value.id
        if base in ea.builtins_aliases and val.attr in _BUILTIN_GUARD_CALLS:
            return val.attr
        if base in ea.importlib_aliases and val.attr == "import_module":
            return "import_module"
        if base in ea.operator_aliases and val.attr in ("attrgetter", "methodcaller"):
            return val.attr
    if isinstance(val, ast.Subscript) and isinstance(val.value, ast.Name):
        if val.value.id in ea.builtins_aliases and isinstance(val.slice, ast.Constant):
            if isinstance(val.slice.value, str) and val.slice.value in _BUILTIN_GUARD_CALLS:
                return val.slice.value
    return None


def _is_sys_modules_expr(node: ast.AST, st: AliasState, ea: EscapeAliases) -> bool:
    if isinstance(node, ast.Name):
        return node.id in ea.modules_aliases
    return (
        isinstance(node, ast.Attribute) and node.attr == "modules" and is_sys_expr(node.value, st)
    )


def _propagate_escape_assign(node: ast.Assign, st: AliasState, ea: EscapeAliases) -> None:
    if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
        return
    tid, val = node.targets[0].id, node.value
    kind = _resolve_escape_fn(val, ea)
    if kind is not None:
        ea.fn_aliases[tid] = kind
    if _is_sys_modules_expr(val, st, ea):
        ea.modules_aliases.add(tid)
    elif (
        isinstance(val, ast.Call)
        and isinstance(val.func, ast.Attribute)
        and val.func.attr == "get"
        and _is_sys_modules_expr(val.func.value, st, ea)
    ):
        ea.dyn_mod_vars.add(tid)
    elif isinstance(val, ast.Name):
        for group in (
            ea.builtins_aliases,
            ea.importlib_aliases,
            ea.operator_aliases,
            ea.runpy_aliases,
            ea.os_aliases,
            ea.dyn_mod_vars,
        ):
            if val.id in group:
                group.add(tid)


def collect_escape_aliases(tree: ast.AST, st: AliasState) -> EscapeAliases:
    """Collect local aliases to builtins, importlib, operator, runpy, and reflection helpers."""
    ea = EscapeAliases(os_aliases=set(st.os_aliases))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                local = a.asname or a.name.split(".")[0]
                if a.name == "builtins":
                    ea.builtins_aliases.add(local)
                elif a.name in ("importlib", "importlib.util", "importlib.machinery"):
                    ea.importlib_aliases.add(local)
                elif a.name == "operator":
                    ea.operator_aliases.add(local)
                elif a.name == "runpy":
                    ea.runpy_aliases.add(local)
                elif a.name == "os":
                    ea.os_aliases.add(local)
        elif isinstance(node, ast.ImportFrom) and node.module:
            for a in node.names:
                local = a.asname or a.name
                if node.module == "importlib" and a.name == "import_module":
                    ea.fn_aliases[local] = "import_module"
                elif node.module == "operator" and a.name in ("attrgetter", "methodcaller"):
                    ea.fn_aliases[local] = a.name
                elif node.module == "builtins" and a.name in _BUILTIN_GUARD_CALLS:
                    ea.fn_aliases[local] = a.name
                elif node.module == "sys" and a.name in ("modules", "*"):
                    ea.modules_aliases.add("modules" if a.name == "*" else local)
    for _ in range(4):
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                _propagate_escape_assign(node, st, ea)
    return ea


def is_tracked_sensitive_obj(node: ast.AST, st: AliasState, ea: EscapeAliases) -> bool:
    """Check whether node references a tracked security-sensitive module or collection."""
    if is_sys_expr(node, st) or is_sys_path_expr(node, st):
        return True
    if isinstance(node, ast.Name):
        tracked = (
            ea.builtins_aliases
            | ea.importlib_aliases
            | ea.operator_aliases
            | ea.runpy_aliases
            | ea.os_aliases
            | ea.modules_aliases
            | ea.dyn_mod_vars
            | st.os_aliases
            | st.site_aliases
            | st.pathlib_aliases
            | _DICT_ACCESS_MODS
        )
        return node.id in tracked
    if isinstance(node, ast.Attribute) and node.attr in (
        "environ",
        "modules",
        "meta_path",
        "path_hooks",
        "path_importer_cache",
        "path",
        "__dict__",
        "__builtins__",
    ):
        return is_tracked_sensitive_obj(node.value, st, ea) or is_sys_expr(node.value, st)
    return False


def _is_safe_globals_get(node: ast.Call, parents: dict[ast.AST, ast.AST], st: AliasState) -> bool:
    p = parents.get(node)
    if not (isinstance(p, ast.Attribute) and p.value is node and p.attr == "get"):
        return False
    gp = parents.get(p)
    if not (isinstance(gp, ast.Call) and gp.func is p and gp.args):
        return False
    key = const_str(gp.args[0], st)
    if key is None or key in _SENSITIVE_ATTRS or key in _SENSITIVE_MODULES:
        return False
    ggp = parents.get(gp)
    if isinstance(ggp, ast.Call) and ggp.func is gp:
        return False
    return not (isinstance(ggp, (ast.Attribute, ast.Subscript)) and ggp.value is gp)


def _check_sys_modules_lookup(
    node: ast.AST,
    st: AliasState,
    ea: EscapeAliases,
    parents: dict[ast.AST, ast.AST],
    lines: list[str],
    edits: list[PathEdit],
    handled: set[ast.AST],
) -> None:
    if isinstance(node, ast.Subscript) and _is_sys_modules_expr(node.value, st, ea):
        key = const_str(node.slice, st)
        if (key is not None and _is_sensitive_module_name(key)) or (
            key is None and isinstance(node.ctx, ast.Load)
        ):
            emit_guard_edit(edits, node, "reflection_escape", lines, handled)
    elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        fv = node.func.value
        if (
            node.func.attr in ("get", "pop", "setdefault")
            and _is_sys_modules_expr(fv, st, ea)
            and node.args
        ):
            key = const_str(node.args[0], st)
            if key is not None and _is_sensitive_module_name(key):
                emit_guard_edit(edits, node, "reflection_escape", lines, handled)
            elif key is None:
                p = parents.get(node)
                if isinstance(p, ast.Attribute) and p.value is node and p.attr in _SENSITIVE_ATTRS:
                    emit_guard_edit(edits, node, "reflection_escape", lines, handled)


def _check_attr_call(kind: str, node: ast.Call, st: AliasState, ea: EscapeAliases) -> bool:
    if kind == "import_module":
        target = (
            node.args[0]
            if node.args
            else next((kw.value for kw in node.keywords if kw.arg == "name"), None)
        )
        mod = const_str(target, st) if target is not None else None
        return mod is None or _is_sensitive_module_name(mod)
    if kind in ("attrgetter", "methodcaller"):
        for arg in node.args:
            val = const_str(arg, st)
            if val is None or any(part in _SENSITIVE_ATTRS for part in val.split(".")):
                return True
        return False
    target = node.args[0] if node.args else None
    attr_node = node.args[1] if len(node.args) >= 2 else None
    attr_val = const_str(attr_node, st) if attr_node is not None else None
    tracked = target is not None and is_tracked_sensitive_obj(target, st, ea)
    if tracked:
        if kind != "getattr" or attr_val is None:
            return True
        return any(part in _SENSITIVE_ATTRS for part in attr_val.split("."))
    if attr_val is not None and any(
        part in _UNTRACKED_SENSITIVE_ATTRS for part in attr_val.split(".")
    ):
        return True
    return False


def _check_call_escape(
    node: ast.Call,
    st: AliasState,
    ea: EscapeAliases,
    parents: dict[ast.AST, ast.AST],
    lines: list[str],
    edits: list[PathEdit],
    handled: set[ast.AST],
) -> None:
    kind = _resolve_escape_fn(node.func, ea)
    if kind is None:
        if isinstance(node.func, ast.Attribute) and node.func.attr in (
            "__getattribute__",
            "__setattr__",
        ):
            emit_guard_edit(edits, node, "reflection_escape", lines, handled)
        return
    if kind in ("__import__", "eval", "exec", "locals"):
        emit_guard_edit(edits, node, "reflection_escape", lines, handled)
    elif kind == "compile" and isinstance(node.func, (ast.Name, ast.Attribute)):
        if isinstance(node.func, ast.Name) or (
            isinstance(node.func.value, ast.Name) and node.func.value.id in ea.builtins_aliases
        ):
            emit_guard_edit(edits, node, "reflection_escape", lines, handled)
    elif kind == "globals":
        if not _is_safe_globals_get(node, parents, st):
            emit_guard_edit(edits, node, "reflection_escape", lines, handled)
    elif kind == "vars":
        if not node.args or is_tracked_sensitive_obj(node.args[0], st, ea):
            emit_guard_edit(edits, node, "reflection_escape", lines, handled)
    elif _check_attr_call(kind, node, st, ea):
        emit_guard_edit(edits, node, "reflection_escape", lines, handled)


def find_reflection_edits(
    tree: ast.AST,
    st: AliasState,
    lines: list[str],
    parents: dict[ast.AST, ast.AST],
    handled: set[ast.AST],
) -> list[PathEdit]:
    """Detect reflection, scope-dict, and dynamic import escape patterns."""
    ea = collect_escape_aliases(tree, st)
    edits: list[PathEdit] = []
    for node in ast.walk(tree):
        _check_sys_modules_lookup(node, st, ea, parents, lines, edits, handled)
        if isinstance(node, ast.Attribute) and node.attr in ("__dict__", "__builtins__"):
            if is_tracked_sensitive_obj(node.value, st, ea):
                emit_guard_edit(edits, node, "reflection_escape", lines, handled)
        elif (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id in ea.dyn_mod_vars
            and node.attr in _SENSITIVE_ATTRS
        ):
            emit_guard_edit(edits, node, "reflection_escape", lines, handled)
        elif isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name):
            if node.value.id in ea.builtins_aliases:
                key = const_str(node.slice, st)
                if key is None or key in _SENSITIVE_ATTRS or key in _BUILTIN_GUARD_CALLS:
                    emit_guard_edit(edits, node, "reflection_escape", lines, handled)
        elif isinstance(node, ast.Call):
            _check_call_escape(node, st, ea, parents, lines, edits, handled)
        elif isinstance(node, (ast.List, ast.Tuple, ast.Set, ast.Dict)):
            elts = node.values if isinstance(node, ast.Dict) else node.elts
            if any(isinstance(e, ast.Name) and e.id in _CONTAINER_TRAP_NAMES for e in elts):
                emit_guard_edit(edits, node, "reflection_escape", lines, handled)
    return edits
