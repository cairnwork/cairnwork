"""Members that import a module by a name given at run time (#370), read as import statements.

A name written as a string literal is as visible as an ``import``: the module it names joins the
closure and the existing checks judge it. A name this reader cannot read (a variable, a call, a
name inside pickled bytes or a file) refuses, and so does a member reached any other way than a
direct call, since its arguments are then unknown. A runner that starts a second collection in
the same process refuses always. So does a loader the reader may lose track of: a local name
bound to it and to something else, its module passed on as a value, or a patcher called unbound.
"""

from __future__ import annotations

import ast
from collections.abc import Callable, Sequence

NOT_STATIC = "NAME_NOT_STATIC: a member that imports by name gets a name this reader cannot read"
UNSEEN = "a member loads code by a name this reader cannot see"
MOCK = ("unittest.mock", "mock")
UNKNOWN = ast.Starred(value=ast.Constant(value=None), ctx=ast.Load())
# member -> (position, keyword) of the argument that names what it imports
BY_NAME = {f"{m}.{n}": (0, "target") for m in MOCK for n in ("patch", "patch.multiple")}
BY_NAME |= {f"{m}.patch.dict": (0, "in_dict") for m in MOCK}
BY_NAME |= {"pytest.importorskip": (0, "modname"), "pydoc.locate": (0, "path")}
BY_NAME |= {"importlib.import_module": (0, "name"), "pkgutil.resolve_name": (0, "name")}
BY_NAME |= {"importlib.util.find_spec": (0, "name")}
IMPORT = {"__import__", "builtins.__import__", "importlib.__import__"}  # fromlist: submodules
BY_NAME |= {m: (0, "name") for m in IMPORT}
LOADS = {"load", "loads", "Unpickler", "_load", "_loads", "_Unpickler"}
NEVER = {f"{m}.{n}" for m in ("pickle", "_pickle") for n in LOADS} | {"shelve.open"}
NEVER |= {"pytest.main", "pytest.console_main", "logging.config.fileConfig"}
NEVER |= {"logging.config.listen", "unittest.main", "unittest.TestProgram"}
HIDDEN = ("importlib.", "pkgutil.", "runpy.", *(f"{m}._" for m in MOCK))  # other members load
DICT_CONFIG = "logging.config.dictConfig"
BY_KEY = {"class", "()", "queue", "listener"}  # dictConfig keys whose string it imports
OWNERS = {*MOCK, *(f"{m}.patch" for m in MOCK), "pytest", "pickle", "_pickle", "shelve"}
OWNERS |= {"pydoc", "logging.config", "importlib", "importlib.util", "pkgutil", "runpy"}
OWNERS |= {"unittest", "builtins", "_pytest.monkeypatch", "pytest.MonkeyPatch"}
OWNERS |= {"_pytest.monkeypatch.MonkeyPatch"}
VALUES = {"builtins"}  # an owner commonly passed as a value: monkeypatch.setattr(builtins, ...)
LOADERS = {"loadTestsFromName": (0, "name"), "loadTestsFromNames": (0, "names")}  # any receiver
ATTR_NEVER = {"discover"}  # TestLoader.discover imports what it finds under a path
PATCHERS = {"setattr": (2, "value"), "delattr": (1, "name")}  # mp.setattr("m.x", v), any receiver
REBINDERS = {"setattr", "delattr", "builtins.setattr", "builtins.delattr"}
REBINDERS |= {f"{m}.{n}" for m in MOCK for n in ("patch", "patch.object", "patch.multiple")}
MAPPINGS = {"os.environ", "sys.modules"}  # patch.dict mappings that are Python's own, not names
NAMED = {*PATCHERS, *LOADERS, *ATTR_NEVER, "__import__"}  # getattr(x, "<one of these>")


def by_name(tree: ast.Module) -> tuple[list[str], str | None]:
    """The dotted names this module's code imports by literal name, and the refusal reason
    when it may import by a name this reader cannot read."""
    aliases, mixed = _aliases(tree)
    names: list[str] = []
    if mixed:
        return names, NOT_STATIC  # one local name bound to a loader and to something else
    calls = {id(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    parts = {id(n.value) for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    for node in ast.walk(tree):
        if _unreadable(node, aliases):
            return names, NOT_STATIC
        if not isinstance(node, ast.Name | ast.Attribute) or id(node) in parts:
            continue
        member = _member(node, aliases)
        attr = _receiver(node)
        loader = _loader(member) or attr in NAMED or member in OWNERS - VALUES
        if loader and id(node) not in calls:
            return names, NOT_STATIC  # passed on or stored: its arguments are not read
    for call in (n for n in ast.walk(tree) if isinstance(n, ast.Call)):
        why = _call(call, aliases, names)
        if why:
            return names, why
    return names, None


def _unreadable(node: ast.AST, aliases: dict[str, str]) -> bool:
    """A star import of a loader's module, the builtins namespace, a module looked up in
    ``sys.modules`` by a name that is not a plain literal, or a rebound mapping."""
    if isinstance(node, ast.ImportFrom) and any(a.name == "*" for a in node.names):
        return node.module in OWNERS
    if isinstance(node, ast.Name) and node.id == "__builtins__":
        return True
    if isinstance(node, ast.Subscript) and _member(node.value, aliases) == "sys.modules":
        key = node.slice
        return not (isinstance(key, ast.Constant) and _plain(key.value))
    if isinstance(node, ast.Subscript) and not isinstance(node.ctx, ast.Load):
        return isinstance(node.slice, ast.Constant) and _mapping(node.slice, aliases)
    if isinstance(node, ast.Attribute | ast.Name) and not isinstance(node.ctx, ast.Load):
        return _member(node, aliases) in MAPPINGS
    return False


def _call(call: ast.Call, aliases: dict[str, str], names: list[str]) -> str | None:
    member = _member(call.func, aliases)
    attr = _receiver(call.func)
    if member in {"getattr", "builtins.getattr"} and call.args:
        key = call.args[1] if len(call.args) > 1 else None
        if _member(call.args[0], aliases) in OWNERS or (
            isinstance(key, ast.Constant) and key.value in NAMED
        ):
            return NOT_STATIC
    if (
        member in NEVER
        or (member.startswith(HIDDEN) and member not in BY_NAME)
        or attr in ATTR_NEVER
    ):
        return UNSEEN
    if member in IMPORT and (len(call.args) > 1 or call.keywords):
        return NOT_STATIC  # a fromlist imports submodules the reader does not name
    if (attr in PATCHERS or member in REBINDERS) and _rebinds(call, aliases):
        return NOT_STATIC  # rebinds a mapping patch.dict trusts
    if member in BY_NAME:
        arg = _arg(call, *BY_NAME[member])
        if member.endswith(".dict") and (isinstance(arg, ast.Dict) or _mapping(arg, aliases)):
            return None  # a mapping, not a name
        return _names([arg], names)
    if attr in LOADERS:
        arg = _arg(call, *LOADERS[attr])
        if attr == "loadTestsFromName":
            return _names([arg], names)
        return _names(arg.elts, names) if isinstance(arg, ast.List | ast.Tuple) else NOT_STATIC
    if isinstance(call.func, ast.Attribute) and attr in PATCHERS:
        receiver = _member(call.func.value, aliases)
        if not receiver or receiver.endswith("MonkeyPatch"):
            return NOT_STATIC  # unbound or on an expression: the arguments may be shifted
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
    found: list[str] = []
    todo: list[object] = [config]
    while todo:
        item = todo.pop()
        if isinstance(item, dict):
            found += [v for k, v in item.items() if k in BY_KEY and isinstance(v, str)]
            todo += item.values()
        elif isinstance(item, list | tuple):
            todo += item
        elif isinstance(item, str) and item.startswith("ext://"):
            found.append(item)
    return _names([ast.Constant(value=n.removeprefix("ext://")) for n in found], names)


def _names(args: Sequence[ast.expr | None], names: list[str]) -> str | None:
    for arg in args:
        value = arg.value if isinstance(arg, ast.Constant) else None
        if not isinstance(value, str) or not _plain(value):
            return NOT_STATIC
        names.append(value.split(":")[0])
    return None


def _plain(value: object) -> bool:
    """A name this reader can follow: absolute, and not itself a loader or a loader's module."""
    if not isinstance(value, str):
        return False
    name = value.split(":")[0]
    return bool(name) and not name.startswith(".") and not _loader(name) and name not in OWNERS


def _loader(member: str) -> bool:
    return (
        member in BY_NAME or member in NEVER or member == DICT_CONFIG or member.startswith(HIDDEN)
    )


def _mapping(arg: ast.AST | None, aliases: dict[str, str]) -> bool:
    if isinstance(arg, ast.Constant):
        return arg.value in MAPPINGS or arg.value in {m.split(".")[1] for m in MAPPINGS}
    return arg is not None and _member(arg, aliases) in MAPPINGS


def _rebinds(call: ast.Call, aliases: dict[str, str]) -> bool:
    given = [*call.args, *(k.value for k in call.keywords)]
    keys = {k.arg for k in call.keywords} & {m.split(".")[1] for m in MAPPINGS}
    return bool(keys) or any(_mapping(a, aliases) for a in given)


def _arg(call: ast.Call, position: int, keyword: str) -> ast.expr | None:
    if any(isinstance(a, ast.Starred) for a in call.args) or any(
        k.arg is None for k in call.keywords
    ):
        return UNKNOWN  # *args or **kwargs: the name may be in there
    if len(call.args) > position:
        return call.args[position]
    return next((k.value for k in call.keywords if k.arg == keyword), None)


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


def _aliases(tree: ast.Module) -> tuple[dict[str, str], bool]:
    """Local name -> what it was imported as, from every import statement in any scope, and
    whether one local name is imported as two things one of which is a loader or its owner."""
    out: dict[str, str] = {}
    seen: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            top = [a.name.split(".")[0] for a in node.names]
            pairs = [
                (a.asname or t, a.name if a.asname else t)
                for a, t in zip(node.names, top, strict=True)
            ]
        elif isinstance(node, ast.ImportFrom):
            module = "." * node.level + (node.module or "")
            pairs = [(a.asname or a.name, f"{module}.{a.name}") for a in node.names]
        else:
            continue
        for local, target in pairs:
            seen.setdefault(local, set()).add(target)
            if not target.startswith("."):
                out[local] = target
    return out, any(len(t) > 1 and any(map(_owned, t)) for t in seen.values())


def _owned(target: str) -> bool:
    return _loader(target) or any(target == o or target.startswith(f"{o}.") for o in OWNERS)


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
