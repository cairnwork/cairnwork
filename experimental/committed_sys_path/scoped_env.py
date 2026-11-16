"""Shared scoped binding, import, and constant-string helpers for write guards."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

from experimental.committed_sys_path.aliases import AliasState, _non_class_nodes
from experimental.committed_sys_path.reflection_guard import const_str

_SUBPROCESS_FUNCS = frozenset(
    {"run", "Popen", "call", "check_call", "check_output", "getoutput", "getstatusoutput"}
)
_ASYNCIO_FUNCS = frozenset({"create_subprocess_exec", "create_subprocess_shell"})
_OS_ENV_FUNCS: dict[str, int] = {
    "execve": 2,
    "execvpe": 2,
    "spawnve": 3,
    "spawnvpe": 3,
    "execle": -1,
    "execlpe": -1,
    "spawnle": -1,
    "spawnlpe": -1,
}


@dataclass
class ScopedEnvState:
    """Shared import, local variable binding, and PYTHONPATH tracking state."""

    os_aliases: set[str] = field(default_factory=set)
    environ_aliases: set[str] = field(default_factory=set)
    putenv_aliases: set[str] = field(default_factory=set)
    getenv_aliases: set[str] = field(default_factory=set)
    subprocess_aliases: set[str] = field(default_factory=set)
    subprocess_fn_aliases: set[str] = field(default_factory=set)
    asyncio_aliases: set[str] = field(default_factory=set)
    asyncio_fn_aliases: set[str] = field(default_factory=set)
    os_exec_aliases: dict[str, int] = field(default_factory=dict)
    functools_aliases: set[str] = field(default_factory=set)
    partial_aliases: set[str] = field(default_factory=set)
    local_env: dict[tuple[int, str], list[ast.AST]] = field(default_factory=dict)
    env_var_names: set[tuple[int, str]] = field(default_factory=set)


def scope_chain(node: ast.AST, parents: dict[ast.AST, ast.AST], mod_id: int) -> tuple[int, ...]:
    """Return tuple of enclosing FunctionDef/AsyncFunctionDef/Module ids from innermost out."""
    chain: list[int] = []
    cur: ast.AST | None = node
    while cur is not None:
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)):
            chain.append(id(cur))
        cur = parents.get(cur)
    if not chain or chain[-1] != mod_id:
        chain.append(mod_id)
    return tuple(chain)


def lookup_local(
    name: str, scopes: tuple[int, ...], local_env: dict[tuple[int, str], list[ast.AST]]
) -> list[ast.AST]:
    """Look up bound RHS expressions in the nearest enclosing scope."""
    for sid in scopes:
        hits = local_env.get((sid, name))
        if hits:
            return hits
    return []


def is_partial_call(node: ast.Call, functools_aliases: set[str], partial_aliases: set[str]) -> bool:
    """Return True if node calls functools.partial or an alias of it."""
    if isinstance(node.func, ast.Name) and node.func.id in partial_aliases:
        return True
    return (
        isinstance(node.func, ast.Attribute)
        and node.func.attr == "partial"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id in functools_aliases
    )


def resolve_str(
    node: ast.AST,
    scopes: tuple[int, ...],
    local_env: dict[tuple[int, str], list[ast.AST]],
    st: AliasState,
    depth: int = 0,
    seen: set[int] | None = None,
) -> str | None:
    """Evaluate constant string/bytes expressions, local names, chr(), decode(), and join()."""
    if depth > 8:
        return None
    if seen is None:
        seen = set()
    nid = id(node)
    if nid in seen:
        return None
    seen.add(nid)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, str):
            return node.value
        if isinstance(node.value, bytes):
            return node.value.decode("latin-1")
        return None
    if isinstance(node, ast.Name):
        cands = lookup_local(node.id, scopes, local_env)
        if len(cands) == 1:
            return resolve_str(cands[0], scopes, local_env, st, depth + 1, seen)
        return const_str(node, st, depth + 1)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = resolve_str(node.left, scopes, local_env, st, depth + 1, seen)
        right = resolve_str(node.right, scopes, local_env, st, depth + 1, seen)
        return None if left is None or right is None else left + right
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for val in node.values:
            sub = val.value if isinstance(val, ast.FormattedValue) else val
            part = resolve_str(sub, scopes, local_env, st, depth + 1, seen)
            if part is None:
                return None
            parts.append(part)
        return "".join(parts)
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name) and node.func.id == "chr" and len(node.args) == 1:
            arg = node.args[0]
            if (
                isinstance(arg, ast.Constant)
                and isinstance(arg.value, int)
                and 0 <= arg.value <= 0x10FFFF
            ):
                return chr(arg.value)
        if isinstance(node.func, ast.Attribute):
            if node.func.attr == "decode":
                return resolve_str(node.func.value, scopes, local_env, st, depth + 1, seen)
            if node.func.attr == "join" and len(node.args) == 1:
                sep = resolve_str(node.func.value, scopes, local_env, st, depth + 1, seen)
                seq = node.args[0]
                if sep is not None and isinstance(seq, (ast.List, ast.Tuple)):
                    items = [
                        resolve_str(e, scopes, local_env, st, depth + 1, seen) for e in seq.elts
                    ]
                    if all(x is not None for x in items):
                        return sep.join(x for x in items if x is not None)
    return None


def is_environ_expr(node: ast.AST, ss: ScopedEnvState, depth: int = 0) -> bool:
    """Return True if node evaluates to os.environ, os.environb, or a shallow copy."""
    if depth > 6:
        return False
    if isinstance(node, ast.Name):
        return node.id in ss.environ_aliases
    if isinstance(node, ast.Attribute):
        return (
            node.attr in ("environ", "environb")
            and isinstance(node.value, ast.Name)
            and node.value.id in ss.os_aliases
        )
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "copy"
    ):
        return is_environ_expr(node.func.value, ss, depth + 1)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "dict":
        return bool(node.args) and is_environ_expr(node.args[0], ss, depth + 1)
    return False


def subprocess_env_arg(node: ast.AST, ss: ScopedEnvState) -> ast.AST | None:
    """Return the env argument AST node of a subprocess/asyncio/os.exec*/os.spawn* call."""
    if not isinstance(node, ast.Call):
        return None
    fn = node.func
    is_sp = (
        isinstance(fn, ast.Attribute)
        and fn.attr in _SUBPROCESS_FUNCS
        and isinstance(fn.value, ast.Name)
        and fn.value.id in ss.subprocess_aliases
    ) or (isinstance(fn, ast.Name) and fn.id in ss.subprocess_fn_aliases)
    is_aio = (
        isinstance(fn, ast.Attribute)
        and fn.attr in _ASYNCIO_FUNCS
        and isinstance(fn.value, ast.Name)
        and fn.value.id in ss.asyncio_aliases
    ) or (isinstance(fn, ast.Name) and fn.id in ss.asyncio_fn_aliases)
    if is_sp or is_aio:
        return next((kw.value for kw in node.keywords if kw.arg == "env"), None)
    env_idx: int | None = None
    if isinstance(fn, ast.Attribute) and fn.attr in _OS_ENV_FUNCS:
        if isinstance(fn.value, ast.Name) and fn.value.id in ss.os_aliases:
            env_idx = _OS_ENV_FUNCS[fn.attr]
    elif isinstance(fn, ast.Name) and fn.id in ss.os_exec_aliases:
        env_idx = ss.os_exec_aliases[fn.id]
    if env_idx is not None:
        kw_env = next((kw.value for kw in node.keywords if kw.arg == "env"), None)
        if kw_env is not None:
            return kw_env
        if env_idx < 0 and len(node.args) >= 2:
            return node.args[env_idx]
        if env_idx >= 0 and len(node.args) > env_idx:
            return node.args[env_idx]
    return None


def _record_env_imports(node: ast.AST, ss: ScopedEnvState) -> None:
    if isinstance(node, ast.Import):
        for a in node.names:
            b = a.asname or a.name.split(".")[0]
            if a.name == "os":
                ss.os_aliases.add(b)
            elif a.name == "subprocess":
                ss.subprocess_aliases.add(b)
            elif a.name == "asyncio":
                ss.asyncio_aliases.add(b)
            elif a.name == "functools":
                ss.functools_aliases.add(b)
    elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
        for a in node.names:
            if a.name == "*":
                continue
            b = a.asname or a.name
            if node.module == "os":
                if a.name in ("environ", "environb"):
                    ss.environ_aliases.add(b)
                elif a.name == "putenv":
                    ss.putenv_aliases.add(b)
                elif a.name == "getenv":
                    ss.getenv_aliases.add(b)
                elif a.name in _OS_ENV_FUNCS:
                    ss.os_exec_aliases[b] = _OS_ENV_FUNCS[a.name]
            elif node.module == "subprocess" and a.name in _SUBPROCESS_FUNCS:
                ss.subprocess_fn_aliases.add(b)
            elif node.module == "asyncio" and a.name in _ASYNCIO_FUNCS:
                ss.asyncio_fn_aliases.add(b)
            elif node.module == "functools" and a.name == "partial":
                ss.partial_aliases.add(b)


def _record_local_binding(name: str, rhs: ast.AST, sid: int, ss: ScopedEnvState) -> None:
    if isinstance(rhs, ast.Name) and rhs.id == name:
        return
    bucket = ss.local_env.setdefault((sid, name), [])
    if len(bucket) < 16 and rhs not in bucket:
        bucket.append(rhs)
    if is_environ_expr(rhs, ss):
        ss.environ_aliases.add(name)
    if (
        isinstance(rhs, ast.Attribute)
        and rhs.attr == "putenv"
        and isinstance(rhs.value, ast.Name)
        and rhs.value.id in ss.os_aliases
    ):
        ss.putenv_aliases.add(name)
    if isinstance(rhs, ast.Name) and rhs.id in ss.putenv_aliases:
        ss.putenv_aliases.add(name)


def collect_scoped_env_state(
    tree: ast.AST, st: AliasState, parents: dict[ast.AST, ast.AST]
) -> ScopedEnvState:
    """Collect module imports, scope-keyed variable bindings, and subprocess env variables."""
    ss = ScopedEnvState(os_aliases=set(st.os_aliases))
    mod_id = id(tree)
    nodes = _non_class_nodes(tree)
    for node in nodes:
        _record_env_imports(node, ss)
    bind_nodes = [
        (node, scope_chain(node, parents, mod_id)[0])
        for node in nodes
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr))
    ]
    for _ in range(4):
        for node, sid in bind_nodes:
            if isinstance(node, ast.Assign):
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name):
                        _record_local_binding(tgt.id, node.value, sid, ss)
            elif (
                isinstance(node, ast.AnnAssign)
                and node.value is not None
                and isinstance(node.target, ast.Name)
            ):
                _record_local_binding(node.target.id, node.value, sid, ss)
            elif isinstance(node, ast.NamedExpr):
                _record_local_binding(node.target.id, node.value, sid, ss)
    for node in ast.walk(tree):
        env_arg = subprocess_env_arg(node, ss)
        if isinstance(env_arg, ast.Name):
            for sid in scope_chain(env_arg, parents, mod_id):
                ss.env_var_names.add((sid, env_arg.id))
    return ss
