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

# Only unconditional, unshadowed module imports may establish module identity.
_PATH_FIRST_OPENERS = frozenset(
    {"os", "io", "builtins", "gzip", "bz2", "lzma", "codecs", "tarfile", "shelve", "dbm"}
)
_MODULES = frozenset(
    {"os", "io", "builtins", "gzip", "bz2", "lzma", "codecs", "tarfile", "shelve", "dbm", "zipfile"}
)
_DIRECT_WRITERS = frozenset({"NamedTemporaryFile", "FileIO", "copyfileobj"})
_MODE_TEXT = re.compile(r"[rwaxbtU+]{1,6}")
_UNBOUND_OPEN = "\0unbound_builtin_open"
_AMBIGUOUS_MODULE = "\0ambiguous_module"
_LEGACY_MODULE_ALIAS = "\0legacy_module_alias:"
_OS_OPEN_PREFIX = "\0os_open:"


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
    if isinstance(call.func, ast.Name) and _OS_OPEN_PREFIX + call.func.id in openers:
        return _mode_is_write(call, default_write=True, flag_mode=True)
    if isinstance(call.func, ast.Name) and call.func.id in flag_openers:
        return _mode_is_write(call, default_write=True, flag_mode=True)
    if isinstance(call.func, ast.Name) and call.func.id in _DIRECT_WRITERS | writers | fileios:
        if call.func.id in fileios:
            return _mode_is_write(call)
        return True
    receiver = call.func.value if isinstance(call.func, ast.Attribute) else None
    attr = call.func.attr if isinstance(call.func, ast.Attribute) else ""
    module = (modules or {}).get(receiver.id, receiver.id) if isinstance(receiver, ast.Name) else ""
    if module.startswith(_LEGACY_MODULE_ALIAS):
        module = module.removeprefix(_LEGACY_MODULE_ALIAS)
        if attr == "open":
            if isinstance(receiver, ast.Name) and receiver.id == "os":
                return True
            return _mode_is_write(call, path_like=True)
    if module == _AMBIGUOUS_MODULE:
        return True
    if isinstance(receiver, ast.Name) and module in _MODULES:
        if attr in {"BZ2File", "LZMAFile", "GzipFile", "ZipFile"}:
            return _mode_is_write(call, module == "zipfile")
        if module in {"shelve", "dbm"} and attr == "open":
            return _mode_is_write(call, default_write=True, mode_index=1, flag_mode=True)
    is_os_open = (
        isinstance(receiver, ast.Name)
        and (receiver.id == "os" or module == "os")
        and attr == "open"
    )
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
        use_single_mode = use_single_mode or (
            isinstance(receiver, ast.Name)
            and module in {"io", "gzip", "builtins", "codecs", "tarfile", "bz2", "lzma"}
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
    mode_keywords = {"mode", "flag"} if flag_mode else {"mode"}
    keyword_modes = [kw.value for kw in call.keywords if kw.arg in mode_keywords]
    if keyword_modes:
        return any(_mode_value_is_write(value, zip_mode, flag_mode) for value in keyword_modes)
    index = 0 if path_like else mode_index
    mode_arg = call.args[index] if len(call.args) > index else None
    if (
        single_mode_arg
        and call.args
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
    return _mode_value_is_write(mode_arg, zip_mode, flag_mode)


def _mode_value_is_write(mode_arg: ast.AST, zip_mode: bool, flag_mode: bool) -> bool:
    if not isinstance(mode_arg, ast.Constant):
        return True
    value = mode_arg.value
    if zip_mode:
        return not isinstance(value, str) or value in {"w", "x", "a"}
    if flag_mode:
        return not isinstance(value, str) or value != "r"
    return not isinstance(value, str) or any(ch in value for ch in ("w", "a", "x", "+"))


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


def _exclusive_imported_names(
    tree: ast.Module, names: frozenset[str], modules: frozenset[str]
) -> frozenset[str]:
    return frozenset(
        alias.asname or alias.name
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module in modules
        for alias in node.names
        if alias.name in names
        and (alias.asname or alias.name) != "FileIO"
        and _has_only_import_binding(tree, alias.asname or alias.name, node, alias)
    )


def _imported_openers(tree: ast.Module) -> frozenset[str]:
    """File-global aliases of file openers; control flow is deliberately ignored."""
    names = frozenset({"open", "BZ2File", "LZMAFile", "GzipFile", "ZipFile", "fdopen"})
    imported = _imported_names(tree, names, _MODULES | {"shelve", "dbm"})
    os_open_aliases = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module == "os"
        for alias in node.names
        if alias.name == "open"
    }
    return (
        imported
        | {_OS_OPEN_PREFIX + name for name in os_open_aliases}
        | ({_UNBOUND_OPEN} if not _module_binds_name(tree, "open") else set())
    )


def _module_aliases(tree: ast.Module) -> dict[str, str]:
    bindings: dict[str, list[tuple[ast.AST, ast.alias, str]]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in _MODULES:
                    bindings.setdefault(alias.asname or alias.name, []).append(
                        (node, alias, alias.name)
                    )
    aliases: dict[str, str] = {}
    for name, imports in bindings.items():
        if len(imports) == 1 and _has_only_import_binding(tree, name, *imports[0][:2]):
            node, alias, module = imports[0]
            aliases[name] = (
                _LEGACY_MODULE_ALIAS + module
                if alias.asname is not None and alias.asname != module
                else module
            )
        else:
            aliases[name] = _AMBIGUOUS_MODULE
    return aliases


def _has_only_import_binding(
    tree: ast.Module, name: str, import_node: ast.AST, import_alias: ast.alias
) -> bool:
    """Trust only one direct module-body import and no other binding of its name."""
    bindings = 0
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Name)
            and node.id == name
            and isinstance(node.ctx, (ast.Store, ast.Del))
        ):
            bindings += 1
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bindings += node.name == name
        elif isinstance(node, ast.arg):
            bindings += node.arg == name
        elif isinstance(node, ast.alias):
            bound = node.asname or node.name.split(".")[0]
            bindings += bound == name
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            bindings += name in node.names
        elif isinstance(node, ast.ExceptHandler):
            bindings += node.name == name
        elif isinstance(node, (ast.MatchAs, ast.MatchStar)):
            bindings += node.name == name
        elif isinstance(node, ast.MatchMapping):
            bindings += node.rest == name
    return (
        bindings == 1
        and isinstance(import_node, (ast.Import, ast.ImportFrom))
        and import_node in tree.body
        and import_alias in import_node.names
    )


_WRITER_REFS = _WRITE_ATTRS | {"open", "FileIO", "write", "writelines", "writestr"}


def _is_writer_reference(node: ast.AST) -> bool:
    """An attribute or name that is a file writer, used as a value rather than called."""
    if isinstance(node, ast.Attribute):
        return node.attr in _WRITER_REFS
    return isinstance(node, ast.Name) and node.id in _DIRECT_WRITERS


def _is_writer_assign(node: ast.AST) -> bool:
    """``w = shutil.copy`` or ``gzip.open = shutil.copy``: a writer rebound where the call
    detector cannot follow it. Refused conservatively (only ever adds a refusal)."""
    if not isinstance(node, ast.Assign):
        return False
    return _is_writer_reference(node.value) or any(_is_writer_reference(t) for t in node.targets)


def _is_write_statement(
    node: ast.AST,
    openers: frozenset[str],
    writers: frozenset[str],
    modules: dict[str, str],
    fileios: frozenset[str],
    flag_openers: frozenset[str],
) -> bool:
    return any(
        _is_writer_assign(part)
        or (
            isinstance(part, ast.Call)
            and _is_write_call(part, openers, writers, modules, fileios, flag_openers)
        )
        for part in ast.walk(node)
    )
