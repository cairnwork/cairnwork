"""Conservative file-writer call detection for candidate source."""

from __future__ import annotations

import ast
import re

_WRITE_ATTRS = frozenset(
    {
        "write_text",
        "write_bytes",
        "copyfile",
        "copy",
        "copy2",
        "copytree",
        "move",
        "rename",
        "replace",
        "symlink",
        "link",
        "extract",
        "extractall",
    }
)

# The imported-name set is file-global and deliberately ignores control flow. A false positive
# is safer than missing a writer hidden behind a conditional import or alias.
_PATH_FIRST_OPENERS = frozenset(
    {"os", "io", "builtins", "gzip", "bz2", "lzma", "codecs", "tarfile", "shelve", "dbm"}
)
_MODULES = frozenset(
    {"os", "io", "builtins", "gzip", "bz2", "lzma", "codecs", "tarfile", "shelve", "dbm", "zipfile"}
)
_DIRECT_WRITERS = frozenset({"NamedTemporaryFile", "FileIO", "copyfileobj"})
_MODE_TEXT = re.compile(r"[rwaxbtU+]{1,6}")
_UNBOUND_OPEN = "\0unbound_builtin_open"


def _is_write_call(
    call: ast.Call,
    openers: frozenset[str] = frozenset(),
    writers: frozenset[str] = frozenset(),
    modules: dict[str, str] | None = None,
    fileios: frozenset[str] = frozenset(),
    flag_openers: frozenset[str] = frozenset(),
) -> bool:
    if isinstance(call.func, ast.Attribute) and call.func.attr in _WRITE_ATTRS | {
        "write",
        "writelines",
        "writestr",
    }:
        return True
    if isinstance(call.func, ast.Name) and call.func.id in _DIRECT_WRITERS | writers | fileios:
        if call.func.id == "FileIO" or call.func.id in fileios:
            return _mode_is_write(call)
        return True
    if isinstance(call.func, ast.Name) and call.func.id in flag_openers:
        return _mode_is_write(call, default_write=True, flag_mode=True)
    receiver = call.func.value if isinstance(call.func, ast.Attribute) else None
    attr = call.func.attr if isinstance(call.func, ast.Attribute) else ""
    module = (modules or {}).get(receiver.id, receiver.id) if isinstance(receiver, ast.Name) else ""
    if isinstance(receiver, ast.Name) and module in _MODULES:
        if attr in {"BZ2File", "LZMAFile", "GzipFile", "ZipFile"}:
            return _mode_is_write(call, module == "zipfile")
        if module in {"shelve", "dbm"} and attr == "open":
            return _mode_is_write(call, default_write=True, mode_index=1, flag_mode=True)
    is_os_open = isinstance(receiver, ast.Name) and module == "os" and attr == "open"
    if is_os_open:
        return True
    if isinstance(receiver, ast.Name) and module == "os" and attr == "fdopen":
        return _mode_is_write(call, mode_index=1)
    is_open = (isinstance(call.func, ast.Name) and call.func.id in {"open", *openers}) or (
        attr == "open"
    )
    if is_open:
        path_like = receiver is not None and (
            not isinstance(receiver, ast.Name) or module not in _PATH_FIRST_OPENERS
        )
        plain_builtin_open = isinstance(call.func, ast.Name) and call.func.id == "open"
        use_single_mode = isinstance(call.func, ast.Name) and not (
            plain_builtin_open and _UNBOUND_OPEN in openers
        )
        return _mode_is_write(call, path_like=path_like, single_mode_arg=use_single_mode)
    return False


def _mode_is_write(
    call: ast.Call,
    zip_mode: bool = False,
    default_write: bool = False,
    mode_index: int = 1,
    path_like: bool = False,
    flag_mode: bool = False,
    single_mode_arg: bool = False,
) -> bool:
    mode_arg = next((kw.value for kw in call.keywords if kw.arg in {"mode", "flag"}), None)
    if mode_arg is None:
        index = 0 if path_like else mode_index
        mode_arg = call.args[index] if len(call.args) > index else None
        if (
            mode_arg is None
            and single_mode_arg
            and len(call.args) == 1
            and not call.keywords
            and isinstance(call.args[0], ast.Constant)
            and isinstance(call.args[0].value, str)
            and _MODE_TEXT.fullmatch(call.args[0].value)
        ):
            mode_arg = call.args[0]
        if path_like and mode_arg is not None:
            first = mode_arg.value if isinstance(mode_arg, ast.Constant) else None
            if isinstance(first, str) and not _MODE_TEXT.fullmatch(first):
                mode_arg = call.args[1] if len(call.args) > 1 else None
    if mode_arg is None:
        if any(isinstance(arg, ast.Starred) for arg in call.args) or any(
            kw.arg is None for kw in call.keywords
        ):
            return True
        return default_write
    if not isinstance(mode_arg, ast.Constant):
        return True
    value = mode_arg.value
    if zip_mode:
        return not isinstance(value, str) or value in {"w", "x", "a"}
    if flag_mode:
        return not isinstance(value, str) or value != "r"
    if not isinstance(value, (str, int)):
        return True
    if isinstance(value, int):
        return value != 0 or default_write
    return any(ch in value for ch in ("w", "a", "x", "+"))


def _module_binds_name(tree: ast.Module, name: str) -> bool:
    """Conservatively detect any module binding that can shadow the builtin name."""
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Name)
            and node.id == name
            and isinstance(node.ctx, (ast.Store, ast.Del))
        ):
            return True
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and node.name == name
        ):
            return True
        if isinstance(node, ast.arg) and node.arg == name:
            return True
        if isinstance(node, ast.alias) and (node.asname or node.name.split(".")[0]) == name:
            return True
        if isinstance(node, (ast.Global, ast.Nonlocal)) and name in node.names:
            return True
        if isinstance(node, ast.ExceptHandler) and node.name == name:
            return True
        if isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name == name:
            return True
        if isinstance(node, ast.MatchMapping) and node.rest == name:
            return True
    return False


def _imported_names(
    tree: ast.Module, names: frozenset[str], modules: frozenset[str]
) -> frozenset[str]:
    return frozenset(
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module in modules
        for alias in node.names
        if alias.name in names
    )


def _imported_openers(tree: ast.Module) -> frozenset[str]:
    """File-global aliases of file openers; control flow is deliberately ignored."""
    names = frozenset({"open", "BZ2File", "LZMAFile", "GzipFile", "ZipFile", "fdopen"})
    imported = _imported_names(tree, names, _MODULES | {"shelve", "dbm"})
    return imported | ({_UNBOUND_OPEN} if not _module_binds_name(tree, "open") else set())


def _module_aliases(tree: ast.Module) -> dict[str, str]:
    return {
        alias.asname or alias.name: alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
        if alias.name in _MODULES
    }


def _is_write_statement(
    node: ast.AST,
    openers: frozenset[str],
    writers: frozenset[str],
    modules: dict[str, str],
    fileios: frozenset[str],
    flag_openers: frozenset[str],
) -> bool:
    return any(
        isinstance(part, ast.Call)
        and _is_write_call(part, openers, writers, modules, fileios, flag_openers)
        for part in ast.walk(node)
    )
