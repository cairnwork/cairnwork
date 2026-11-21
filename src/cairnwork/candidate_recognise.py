"""Recognise test imports whose source and dynamic lookup paths are bounded."""

from __future__ import annotations

import ast

from cairnwork.candidate_recognise_names import TEST_MODULES

_BASE_TOP_LEVEL = frozenset(
    """__future__ abc ast base64 collections colorsys configparser contextlib copy dataclasses
    datetime email enum errno fcntl fnmatch fractions functools getpass grp hashlib hmac html http
    io itertools json math os pathlib posixpath pwd random re resource secrets shlex shutil signal
    socket sqlite3 stat string struct subprocess sys tarfile tempfile textwrap threading time
    tomllib tracemalloc types typing unicodedata unittest urllib uuid warnings zipfile zlib
    pytest hypothesis cryptography cairnwork cairnwork_attest cairnwork_protocol
    cairnwork_sandbox cairnwork_quickstart experimental tests conftest scripts tools product
    """.split()
)
ALLOWED_TOP_LEVEL = _BASE_TOP_LEVEL | TEST_MODULES
EXEC_NAMES = frozenset(
    {"exec", "eval", "compile", "__import__", "__builtins__", "globals", "vars", "locals"}
)
DANGEROUS_ATTRS = frozenset(
    """__dict__ __globals__ f_globals f_builtins f_locals __self__ __loader__ __spec__
    __subclasses__ tb_frame gi_frame cr_frame ag_frame modules meta_path path_hooks
    path_importer_cache""".split()
)
CODE_ATTRS = frozenset({"CodeType", "FunctionType", "LambdaType", "CellType", "new_class"})
TYPING_ATTRS = frozenset({"get_type_hints", "ForwardRef", "_eval_type", "evaluate_forward_ref"})
SQLITE_ATTRS = frozenset({"load_extension", "enable_load_extension"})
FORMATTER_ATTRS = frozenset({"get_field", "vformat", "format_field"})
WARNING_ATTRS = frozenset({"_setoption", "_getcategory"})


def recognised_test_imports(tree: ast.Module) -> set[str]:
    """Return precise dynamic names, or a star when any test behavior is unrecognised."""
    names: set[str] = set()
    called = {id(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    builtins_aliases = {
        alias.asname or "builtins"
        for imported in tree.body
        if isinstance(imported, ast.Import)
        for alias in imported.names
        if alias.name == "builtins"
    }
    os_aliases = {
        alias.asname or alias.name
        for imported in tree.body
        if isinstance(imported, ast.Import)
        for alias in imported.names
        if alias.name == "os"
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots = {a.name.split(".", 1)[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            roots = {node.module.split(".", 1)[0]} if node.module else set()
        else:
            roots = set()
        if roots - ALLOWED_TOP_LEVEL:
            return {"*"}
        if (
            isinstance(node, ast.ImportFrom)
            and node.module == "builtins"
            and any(alias.name in EXEC_NAMES for alias in node.names)
        ):
            return {"*"}
        if (
            isinstance(node, ast.ImportFrom)
            and node.module == "os"
            and any(alias.name == "sys" for alias in node.names)
        ):
            return {"*"}
        if isinstance(node, ast.ImportFrom) and node.module in {
            "types",
            "typing",
            "sqlite3",
            "warnings",
        }:
            restricted = {
                "types": CODE_ATTRS,
                "typing": TYPING_ATTRS,
                "sqlite3": SQLITE_ATTRS,
                "warnings": WARNING_ATTRS,
            }[node.module]
            if any(alias.name in restricted for alias in node.names):
                return {"*"}
        if isinstance(node, ast.Import):
            names.update(part for alias in node.names for part in alias.name.split("."))
        elif isinstance(node, ast.ImportFrom):
            names.update((node.module or "").split("."))
            names.update(alias.name for alias in node.names if alias.name != "*")
        if _dangerous_use(node, called, builtins_aliases, os_aliases):
            return {"*"}
        if isinstance(node, ast.Call):
            extra, star = _exit_effect(node, tree)
            if star:
                return {"*"}
            names.update(extra)
    return names


def _dangerous_use(
    node: ast.AST,
    called: set[int],
    builtins_aliases: set[str],
    os_aliases: set[str],
) -> bool:
    if isinstance(node, ast.Name) and node.id in EXEC_NAMES:
        return id(node) not in called
    if isinstance(node, ast.Name) and node.id == "getattr":
        return id(node) not in called
    if isinstance(node, ast.Attribute):
        if node.attr in DANGEROUS_ATTRS | CODE_ATTRS:
            return True
        if node.attr in EXEC_NAMES and isinstance(node.value, ast.Name):
            return node.value.id == "builtins" or node.value.id in builtins_aliases
        if (
            node.attr == "sys"
            and isinstance(node.value, ast.Name)
            and (node.value.id == "os" or node.value.id in os_aliases)
        ):
            return True
        if node.attr == "getattr":
            return id(node) not in called
    if isinstance(node, ast.Call) and _call_name(node.func) == "getattr":
        if len(node.args) < 2 or not _string(node.args[1]):
            return True
        return _string(node.args[1]) in DANGEROUS_ATTRS | EXEC_NAMES | CODE_ATTRS
    return False


def _exit_effect(node: ast.Call, tree: ast.Module) -> tuple[set[str], bool]:
    name = _call_name(node.func)
    attr = node.func.attr if isinstance(node.func, ast.Attribute) else ""
    builtin_attribute = (
        isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "builtins"
    )
    if name == "__import__":
        return set(), True
    if name in {"exec", "eval", "compile"} and (
        isinstance(node.func, ast.Name) or builtin_attribute
    ):
        source = _string(node.args[0]) if node.args else None
        if source is None:
            return set(), True
        try:
            nested = ast.parse(source, filename="tests/test_inline.py")
        except (SyntaxError, ValueError):
            return set(), True
        extra = recognised_test_imports(nested)
        return extra, "*" in extra
    if name == "getattr":
        return set(), False
    if name in TYPING_ATTRS | CODE_ATTRS | SQLITE_ATTRS | WARNING_ATTRS:
        return set(), True
    if attr in FORMATTER_ATTRS and "Formatter" in ast.dump(node.func):
        return set(), True
    if name in {
        "setattr",
        "delattr",
        "patch",
        "importorskip",
        "loadTestsFromName",
        "loadTestsFromNames",
    }:
        target = node.args[0] if node.args else None
        value = _string(target)
        if name in {"setattr", "delattr"} and not isinstance(node.func, ast.Attribute):
            return set(), False  # the builtin object form, not a string import target
        if value is None and name in {"setattr", "delattr"} and len(node.args) >= 3:
            if _string(node.args[1]) is not None:
                return set(), False  # monkeypatch.setattr(obj, "attr", value)
        if value is None:
            return set(), True
        if _machinery_target(value):
            return set(), True
        return _prefixes(value), False
    if name == "discover":
        return set(), True
    if name == "main" and isinstance(node.func, ast.Attribute):
        if any(k.arg == "module" for k in node.keywords):
            val = next(k.value for k in node.keywords if k.arg == "module")
            target = _string(val)
            return (_prefixes(target), False) if target is not None else (set(), True)
        if any(k.arg == "defaultTest" for k in node.keywords):
            return set(), True
    if name == "main" and isinstance(node.func, ast.Attribute) and "pytest" in ast.dump(node.func):
        return _pytest_plugins(node)
    return set(), False


def _pytest_plugins(node: ast.Call) -> tuple[set[str], bool]:
    if not node.args or not isinstance(node.args[0], (ast.List, ast.Tuple)):
        return set(), True
    names: set[str] = set()
    items = node.args[0].elts
    index = 0
    while index < len(items):
        item = items[index]
        value = _string(item)
        if value is None:
            return set(), True
        if value == "-p":
            index += 1
            if index >= len(items) or _string(items[index]) is None:
                return set(), True
            plugin = _string(items[index]) or ""
            if _machinery_target(plugin):
                return set(), True
            names.update(_prefixes(plugin))
        elif value.startswith("-p"):
            plugin = value[2:]
            if _machinery_target(plugin):
                return set(), True
            names.update(_prefixes(plugin))
        index += 1
    return names, False


def _machinery_target(value: str) -> bool:
    parts = value.split(".")
    return value in {"sys.modules", "sys.meta_path"} or (
        len(parts) == 2 and parts[0] == "builtins" and parts[1] in EXEC_NAMES
    )


def _prefixes(value: str) -> set[str]:
    parts = value.lstrip(".").split(".")
    return {".".join(parts[:i]) for i in range(1, len(parts) + 1) if parts[i - 1]}


def _string(node: ast.AST | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _call_name(node: ast.expr) -> str:
    return (
        node.attr
        if isinstance(node, ast.Attribute)
        else node.id
        if isinstance(node, ast.Name)
        else ""
    )
