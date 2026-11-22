"""Resolve the names a test calls to the project's modules, by reading source only (ast).

A blackbox check names a module and a top-level function; the runner imports that module from the
checkout and calls ``getattr(module, function)``. So a draft keeps the module the test imported
from (what the test itself calls) and only needs to know what the name is there: a ``def``
(function kind), a ``class`` (constructor: method kind), or something this reader cannot tell
(refused). Nothing is imported: modules are looked up as files under the project's source roots.
"""

from __future__ import annotations

import ast
import importlib.machinery
from dataclasses import dataclass
from pathlib import Path

from .literals import Refused

MAX_HOPS = 6


MODULE_HOOKS = {"__getattr__", "__dir__"}
# Names that can bind a module attribute under a name the reader cannot see (#370).
DYNAMIC = {"exec", "eval", "globals", "locals", "vars", "setattr", "delattr", "__import__"}
DYNAMIC |= {"__builtins__", "import_module", "attrgetter", "methodcaller"}
REFLECTIVE = {"__dict__", "__globals__", "__self__", "__setattr__", "__getattribute__", "modules"}
REFLECTIVE |= {"f_globals", "f_locals", "f_builtins"}
INTROSPECTION = {"builtins", "ctypes", "gc", "inspect"}  # modules refused when imported at all
SEARCH = {"__path__", "submodule_search_locations"}  # where a package finds its submodules


@dataclass(frozen=True)
class Target:
    module: str  # what the test imported from: the module the runner will import
    name: str
    kind: str  # "function" or "class"
    defined_in: str = ""  # the module whose source defines it (an exception's __module__)


class Project:
    """The project's source tree: module name -> parsed file, cached."""

    def __init__(self, roots: list[Path]):
        self.roots = roots
        self._cache: dict[str, ast.Module | None] = {}

    def module_file(self, module: str) -> Path | None:
        parts = module.split(".")
        for root in self.roots:
            for path in (
                root.joinpath(*parts).with_suffix(".py"),
                root.joinpath(*parts, "__init__.py"),
            ):
                if path.is_file():
                    return path
        return None

    def tree(self, module: str) -> ast.Module | None:
        if module not in self._cache:
            path = self.module_file(module)
            try:
                self._cache[module] = ast.parse(path.read_bytes()) if path else None
            except (SyntaxError, ValueError, OSError):
                self._cache[module] = None
        return self._cache[module]

    def is_package_module(self, module: str) -> bool:
        return self.module_file(module) is not None

    def files(self, module: str) -> list[Path]:
        """Every file Python could import as module, in the first root that holds one."""
        *head, last = module.split(".")
        for root in self.roots:
            found = [
                path
                for suffix in importlib.machinery.all_suffixes()
                for path in (
                    root.joinpath(*head, last + suffix),
                    root.joinpath(*head, last, "__init__" + suffix),
                )
                if path.is_file()
            ]
            if found:
                return found
        return []

    def import_closure(self, modules: set[str], anywhere: bool = False) -> set[str]:
        """The project modules that importing modules surely imports: each one, its parent
        packages, and what their top-level import statements name, transitively. Imports under
        if/try/def are left out, so this may be too small, never too large. With anywhere, every
        import statement is followed and a module with files but no readable source is kept, so
        it may be too large instead (#370)."""
        found: set[str] = set()
        todo = list(modules)
        while todo and len(found) < 10000:
            module = todo.pop()
            parts = module.split(".")
            for name in (".".join(parts[: i + 1]) for i in range(len(parts))):
                tree = self.tree(name)
                if name in found or (tree is None and not (anywhere and self.files(name))):
                    continue
                found.add(name)
                if tree is not None:
                    todo += _imported(tree, name, self.module_file(name), anywhere)
        return found

    def kind_of(self, module: str, name: str, hops: int = 0) -> tuple[str, str, str]:
        """(kind, defining module, name there): kind is 'function' or 'class', following
        re-exports, star imports and simple aliases."""
        if hops > MAX_HOPS:
            raise Refused(f"{name!r} is re-exported through too many modules")
        tree = self.tree(module)
        if tree is None:
            raise Refused(f"{module} is not a module of the project (stdlib or a dependency?)")
        path = self.module_file(module)
        package = path is not None and path.name == "__init__.py"
        found = _defined(tree, module, name, package)
        if found is None:
            for star in _star_imports(tree, module, package):
                try:
                    return self.kind_of(star, name, hops + 1)
                except Refused:
                    continue
            raise Refused(f"cannot find {name!r} in {module} by reading its source")
        if found[0] in ("function", "class"):
            return found
        return self.kind_of(found[1], found[2], hops + 1)


def _defined(tree: ast.Module, module: str, name: str, package: bool):
    """The last top-level binding of name: ('function'|'class', m, n) or ('import', m, n)."""
    found: tuple[str, str, str] | None = None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            found = ("function", module, name)
        elif isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            found = ("async", module, name)
        elif isinstance(node, ast.ClassDef) and node.name == name:
            found = ("class", module, name)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if (alias.asname or alias.name) == name:
                    found = ("import", _absolute(module, node, package), alias.name)
        elif (isinstance(node, ast.Assign) and any(_is_name(t, name) for t in node.targets)) or (
            isinstance(node, ast.AnnAssign) and _is_name(node.target, name)
        ):
            found = (
                ("import", module, node.value.id)
                if isinstance(node.value, ast.Name)
                else ("other", module, name)
            )
    if found and found[0] in ("async", "other"):
        raise Refused(f"{name!r} in {module} is not a plain def or class")
    return found


def _imported(tree: ast.Module, module: str, path: Path | None, anywhere: bool) -> list[str]:
    """Modules named by the top-level (or with anywhere, all) import statements of module
    (submodules included)."""
    package = path is not None and path.name == "__init__.py"
    out: list[str] = []
    for node in ast.walk(tree) if anywhere else tree.body:
        if isinstance(node, ast.Import):
            out += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = _absolute(module, node, package)
            out += [base, *(f"{base}.{a.name}" for a in node.names if a.name != "*")]
    return out


def _star_imports(tree: ast.Module, module: str, package: bool) -> list[str]:
    return [
        _absolute(module, node, package)
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and any(a.name == "*" for a in node.names)
    ]


def binds_module_hook(tree: ast.Module) -> bool:
    """Whether a module may bind ``__getattr__`` or ``__dir__`` as module attributes (#370):
    any mention of either name outside a class or function body, a ``global`` or attribute
    store of either, either name as a string, or anything, anywhere in the module, that can
    bind a module attribute under a name the reader cannot see (``exec``, ``globals()``,
    ``setattr``, ``sys.modules``, a ``__dict__`` or ``__globals__``, a store to ``__class__``,
    an import of ``builtins``, ``ctypes``, ``gc`` or ``inspect``, a ``getattr`` with a computed
    name), or any mention of a package's ``__path__``. A method in a class body is not one."""
    plain = {
        id(node.func)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and _is_name(node.func, "getattr")
        and len(node.args) > 1
        and isinstance(node.args[1], ast.Constant)
    }
    return _mentions_hook(tree) or any(_binds(node, plain) for node in ast.walk(tree))


def _binds(node: ast.AST, plain: set[int]) -> bool:
    if isinstance(node, ast.Global):
        return bool(MODULE_HOOKS & set(node.names))
    if isinstance(node, ast.Name):
        return node.id in DYNAMIC | SEARCH or (node.id == "getattr" and id(node) not in plain)
    if isinstance(node, ast.Attribute):
        hooks = MODULE_HOOKS | {"__class__"}
        store = isinstance(node.ctx, ast.Store) and node.attr in hooks
        return store or node.attr in DYNAMIC | REFLECTIVE | SEARCH
    if isinstance(node, ast.alias):
        return node.name.split(".")[0] in INTROSPECTION or node.name in DYNAMIC | REFLECTIVE
    if isinstance(node, ast.ImportFrom):
        return (node.module or "").split(".")[0] in INTROSPECTION
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value in MODULE_HOOKS | REFLECTIVE | SEARCH
    return False


def _mentions_hook(node: ast.AST) -> bool:
    """A def/class name, or any name field (Name, alias, except/match targets) at module scope."""
    scope = isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
    for field, value in ast.iter_fields(node):
        if scope and field == "body":
            continue
        for item in value if isinstance(value, list) else [value]:
            if isinstance(item, str) and item in MODULE_HOOKS:
                return True
            if isinstance(item, ast.AST) and _mentions_hook(item):
                return True
    return False


def _is_name(target: ast.expr, name: str) -> bool:
    return isinstance(target, ast.Name) and target.id == name


def _absolute(module: str, node: ast.ImportFrom, package: bool) -> str:
    """The absolute module of a (possibly relative) from-import inside module."""
    if not node.level:
        return node.module or ""
    parts = module.split(".")
    base = parts[: len(parts) - node.level + (1 if package else 0)]
    return ".".join(base + ([node.module] if node.module else []))


class Imports:
    """What the test module's top-level imports bind: local name -> ('module', m) or
    ('from', m, n). Relative imports in tests are refused (test helpers, not the project)."""

    def __init__(self, tree: ast.Module):
        self.names: dict[str, tuple[str, ...]] = {}
        self.local_defs: set[str] = set()
        for node in tree.body:  # in order: a later binding replaces an import
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.asname:
                        self.names[alias.asname] = ("module", alias.name)
                    else:
                        top = alias.name.split(".")[0]
                        self.names[top] = ("module", top)
                    self.local_defs.discard(alias.asname or alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
                for alias in node.names:
                    self.names[alias.asname or alias.name] = ("from", node.module, alias.name)
                    self.local_defs.discard(alias.asname or alias.name)
            else:
                for name in _bound_names(node):
                    self.names.pop(name, None)
                    self.local_defs.add(name)


def _bound_names(node: ast.stmt) -> list[str]:
    """Names a module-level statement binds (relative imports included: test helpers)."""
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
        return [node.name]
    if isinstance(node, ast.ImportFrom):
        return [a.asname or a.name for a in node.names]
    return [
        n.id for n in ast.walk(node) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)
    ]


def dotted(node: ast.expr) -> list[str] | None:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    return [node.id, *reversed(parts)]


def resolve_callee(func: ast.expr, imports: Imports, project: Project) -> Target:
    """The project target of a call expression's func (``f``, ``mod.f``, ``pkg.mod.f``)."""
    parts = dotted(func)
    if parts is None:
        raise Refused("the call target is not a plain name or dotted name")
    head, *rest = parts
    if head in imports.local_defs:
        raise Refused(f"{head!r} is defined or rebound in the test file, not the project's")
    bound = imports.names.get(head)
    if bound is None:
        raise Refused(f"{head!r} is not imported at the top of the test file (builtin or local)")
    if bound[0] == "from":
        module, name = bound[1], bound[2]
        sub = f"{module}.{name}"
        if rest and project.is_package_module(sub):  # from pkg import mod; mod.f()
            module, path = sub, rest
        elif rest:
            raise Refused(f"{'.'.join(parts)} is an attribute of {name!r}, not a module function")
        else:
            path = [name]
    else:
        module, path = bound[1], rest
        while len(path) > 1 and project.is_package_module(f"{module}.{path[0]}"):
            module, path = f"{module}.{path[0]}", path[1:]
    if len(path) != 1:
        raise Refused(f"{'.'.join(parts)} is not a top-level name of a project module")
    kind, defined_in, real = project.kind_of(module, path[0])
    return Target(module, path[0], kind, f"{defined_in}.{real}")


def module_env(tree: ast.Module) -> dict[str, ast.expr]:
    """Module-level names assigned exactly once (their values are evaluated only where used)."""
    seen: dict[str, ast.expr] = {}
    twice: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name):
                twice |= {target.id} & set(seen)
                seen[target.id] = node.value
                continue
        if isinstance(node, ast.Import):  # any other binding (def, class, import, X += ...)
            twice |= {a.asname or a.name.split(".")[0] for a in node.names}
        else:
            twice |= set(_bound_names(node))
    return {name: value for name, value in seen.items() if name not in twice}
