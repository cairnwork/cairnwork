"""Members that import a module by a name given at run time (#370), read as import statements.

A name written as a string literal is as visible as an ``import``: the module it names joins the
closure and the existing checks judge it. A name this reader cannot read (a variable, a call, a
name inside pickled bytes or a file) refuses, and so does a member reached any other way than a
direct call, since its arguments are then unknown. A runner that starts a second collection in
the same process refuses always.
"""

from __future__ import annotations

import ast
import sys
from collections.abc import Callable, Sequence

NOT_STATIC = "NAME_NOT_STATIC: a member that imports by name gets a name this reader cannot read"
UNSEEN = "a member loads code by a name this reader cannot see"
MOCK = ("unittest.mock", "mock")
UNKNOWN = ast.Starred(value=ast.Constant(value=None), ctx=ast.Load())
# member -> (position, keyword) of the argument that names what it imports
BY_NAME = {f"{m}.{n}": (0, "target") for m in MOCK for n in ("patch", "patch.multiple")}
BY_NAME |= {f"{m}.patch.dict": (0, "in_dict") for m in MOCK}
BY_NAME |= {"pytest.importorskip": (0, "modname"), "pydoc.locate": (0, "path")}
LOADS = {"load", "loads", "Unpickler", "_load", "_loads", "_Unpickler"}
NEVER = {f"{m}.{n}" for m in ("pickle", "_pickle") for n in LOADS} | {"shelve.open"}
NEVER |= {"pytest.main", "pytest.console_main", "logging.config.fileConfig"}
NEVER |= {"logging.config.listen"}
DICT_CONFIG = "logging.config.dictConfig"
BY_KEY = {"class", "()", "queue", "listener"}  # dictConfig keys whose string it imports
OWNERS = {*MOCK, *(f"{m}.patch" for m in MOCK), "pytest", "pickle", "_pickle", "shelve"}
OWNERS |= {"pydoc", "logging.config", "monkeypatch"}
LOADERS = {"loadTestsFromName": (0, "name"), "loadTestsFromNames": (0, "names")}  # any receiver
PATCHERS = {"setattr": (2, "value"), "delattr": (1, "name")}  # monkeypatch.setattr("m.x", v)


def by_name(tree: ast.Module) -> tuple[list[str], str | None]:
    """The dotted names this module's code imports by literal name, and the refusal reason
    when it may import by a name this reader cannot read."""
    aliases = _aliases(tree)
    names: list[str] = []
    calls = {id(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    parts = {id(n.value) for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and any(a.name == "*" for a in node.names):
            if node.module in OWNERS:
                return names, NOT_STATIC
        if not isinstance(node, ast.Name | ast.Attribute) or id(node) in parts:
            continue
        member = _member(node, aliases)
        called = id(node) in calls
        loader = member in BY_NAME or member in NEVER or member == DICT_CONFIG
        patcher = _receiver(node) in PATCHERS and member.startswith("monkeypatch.")
        if (loader or patcher or _receiver(node) in LOADERS) and not called:
            return names, NOT_STATIC  # passed on or stored: its arguments are not read
    for call in (n for n in ast.walk(tree) if isinstance(n, ast.Call)):
        why = _call(call, aliases, names)
        if why:
            return names, why
    return names, None


def _call(call: ast.Call, aliases: dict[str, str], names: list[str]) -> str | None:
    member = _member(call.func, aliases)
    if member == "getattr" and call.args and _member(call.args[0], aliases) in OWNERS:
        return NOT_STATIC
    if member in NEVER:
        return UNSEEN
    if member in BY_NAME:
        arg = _arg(call, *BY_NAME[member])
        if member.endswith(".dict") and (isinstance(arg, ast.Dict) or _stdlib(arg, aliases)):
            return None  # a mapping, not a name
        return _names([arg], names)
    attr = _receiver(call.func)
    if attr in LOADERS:
        arg = _arg(call, *LOADERS[attr])
        if attr == "loadTestsFromName":
            return _names([arg], names)
        return _names(arg.elts, names) if isinstance(arg, ast.List | ast.Tuple) else NOT_STATIC
    if attr in PATCHERS and member.startswith("monkeypatch."):
        given = _arg(call, *PATCHERS[attr])
        if given is not None and not isinstance(given, ast.Starred):
            return None  # an object and an attribute name: nothing is imported
        return _names([_arg(call, 0, "target")], names)
    if member == DICT_CONFIG:
        return _config(_arg(call, 0, "config"), names)
    return None


def _config(arg: ast.expr | None, names: list[str]) -> str | None:
    """dictConfig: only a literal config, whose class and factory strings become names."""
    try:
        config = ast.literal_eval(arg) if isinstance(arg, ast.Dict) else None
    except (ValueError, TypeError, SyntaxError, RecursionError):
        config = None
    if not isinstance(config, dict):
        return NOT_STATIC
    todo: list[object] = [config]
    while todo:
        item = todo.pop()
        if isinstance(item, dict):
            names += [v for k, v in item.items() if k in BY_KEY and isinstance(v, str)]
            todo += item.values()
        elif isinstance(item, list | tuple):
            todo += item
        elif isinstance(item, str) and item.startswith("ext://"):
            names.append(item.removeprefix("ext://"))
    return None


def _names(args: Sequence[ast.expr | None], names: list[str]) -> str | None:
    for arg in args:
        if not (isinstance(arg, ast.Constant) and isinstance(arg.value, str)):
            return NOT_STATIC
        names.append(arg.value)
    return None


def _arg(call: ast.Call, position: int, keyword: str) -> ast.expr | None:
    if any(isinstance(a, ast.Starred) for a in call.args) or any(
        k.arg is None for k in call.keywords
    ):
        return UNKNOWN  # *args or **kwargs: the name may be in there
    if len(call.args) > position:
        return call.args[position]
    return next((k.value for k in call.keywords if k.arg == keyword), None)


def _stdlib(arg: ast.expr | None, aliases: dict[str, str]) -> bool:
    """A standard-library module attribute (``os.environ``): its value is Python's own."""
    if not isinstance(arg, ast.Attribute):
        return False
    top = _member(arg, aliases).split(".")[0]
    return top in sys.stdlib_module_names and top not in ("pickle", "_pickle", "shelve")


def _receiver(node: ast.AST) -> str:
    return node.attr if isinstance(node, ast.Attribute) else ""


def _member(node: ast.AST | str, aliases: dict[str, str]) -> str:
    """The dotted name an expression spells, its first part read through the imports."""
    if isinstance(node, str):
        return node
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return ""
    return ".".join([aliases.get(node.id, node.id), *reversed(parts)])


def _aliases(tree: ast.Module) -> dict[str, str]:
    """Local name -> what it was imported as, from every import statement (not scope-aware)."""
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                out[a.asname or a.name.split(".")[0]] = a.name if a.asname else a.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            out |= {a.asname or a.name: f"{node.module}.{a.name}" for a in node.names}
    return out


def as_imports(names: list[str], is_module: Callable[[str], bool]) -> ast.Module:
    """Import statements equal to what the names make Python import: the longest dotted prefix
    that is a module of the project, else the first part (as mock, pytest and pkgutil resolve)."""
    body: list[ast.stmt] = []
    for name in names:
        parts = name.split(".")
        prefixes = (".".join(parts[:i]) for i in range(len(parts), 0, -1))
        module = next((p for p in prefixes if is_module(p)), parts[0])
        body.append(ast.Import(names=[ast.alias(name=module)]))
    return ast.Module(body=body, type_ignores=[])
