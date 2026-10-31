"""What runs when pytest imports a test module, a conftest or a test package's ``__init__``.

Any code that runs there can change what every test sees, and a blackbox case repeats only the
call. So this is an allow-list, not a list of known dangers: a module keeps its tests declarable
only if every top-level statement is one of a few inert shapes (docstrings; imports of pytest,
unittest, the standard library or the project; assignments of literals, names and pytest marks;
``def`` and ``class`` whose decorators, decorator arguments, defaults and annotations are such
literals or plain types and whose bases are defined in the file, ``object`` or unittest's
``TestCase``; ``if TYPE_CHECKING:`` imports, with the flag imported from ``typing`` and never
bound otherwise). Names that pytest or Python call implicitly (hooks, setup functions, a module's
``__getattr__``, a class's ``__init_subclass__``) are allowed only where a ``def`` of them is
read elsewhere. Anything else refuses every test in its scope, and statements that may define or
collect tests mark the count inexact. Nothing is executed: the modules are parsed with ``ast``.
"""

from __future__ import annotations

import ast
import sys
from collections.abc import Callable

from .obligations import CLASS_HOOKS, SETUP_HOOKS
from .resolve import dotted

COUNT_HOOKS = {  # change what is collected
    "pytest_generate_tests",
    "pytest_collection_modifyitems",
    "pytest_pycollect_makeitem",
    "pytest_collect_file",
    "pytest_ignore_collect",
    "pytest_collection",
}
HARMLESS_HOOKS = {"pytest_addoption", "pytest_report_header", "pytest_assertrepr_compare"}
PYTEST_HEADS = {"pytest", "mark", "fixture", "param"}
SAFE_IMPORTS = {"pytest", "unittest", "_pytest"} | set(sys.stdlib_module_names)
TYPING_NAMES = {"TYPE_CHECKING": "typing.TYPE_CHECKING", "typing": "typing"}
MODULE_DUNDERS = {"__getattr__", "__dir__"}  # pytest's collection looks names up on the module
CLASS_DUNDERS = {"__init_subclass__", "__class_getitem__"}  # run on subclassing, subscripting
GENERICS = {"list", "dict", "tuple", "set", "frozenset", "type"}
GENERIC_ORIGINS = ("typing.", "collections.abc.")


class Verdict:
    """The first reason found (None: inert), whether the count stays known, and the project
    modules imported (each one's import-time code runs too: see ``imports``)."""

    def __init__(self) -> None:
        self.reason: str | None = None
        self.known = True
        self.imports: set[str] = set()
        self.project_names: set[str] = set()  # local names bound by those imports
        self.classes: set[str] = set()  # module-level classes here: bases besides _safe_base
        self.origins: dict[str, str] = {}  # name -> the import binding it, "" if bound otherwise

    def refuse(self, reason: str, *, known: bool = True) -> None:
        self.reason = self.reason or reason
        self.known = self.known and known


def _calls_outside_pytest(node: ast.AST) -> bool:
    return any(
        isinstance(n, ast.Call) and (dotted(n.func) or [""])[0] not in PYTEST_HEADS
        for n in ast.walk(node)
    )


def _calls_other_than_marks(node: ast.AST) -> bool:
    """A call in a module-level value other than building a mark or a param."""
    for n in ast.walk(node):
        parts = (dotted(n.func) or [""]) if isinstance(n, ast.Call) else ["pytest", "mark"]
        parts = parts[1:] if parts[0] == "pytest" else parts
        if parts[:1] not in (["mark"], ["param"]):
            return True
    return False


def _inert(node: ast.expr, verdict: Verdict) -> bool:
    """Building this value runs no project code: constants, containers and signs of them, names
    not bound by a project import, and pytest marks or params built from those."""
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, ast.Name):
        return node.id not in verdict.project_names
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub | ast.UAdd):
        return isinstance(node.operand, ast.Constant)
    if isinstance(node, ast.List | ast.Tuple | ast.Set):
        return all(_inert(e, verdict) for e in node.elts)
    if isinstance(node, ast.Dict):
        keys = [k for k in node.keys if k is not None]  # None: ** unpacking, which iterates
        return len(keys) == len(node.keys) and all(_inert(e, verdict) for e in keys + node.values)
    if isinstance(node, ast.Call) and any(k.arg is None for k in node.keywords):
        return False  # ** calls the mapping's keys() at import time
    args = [*node.args, *(k.value for k in node.keywords)] if isinstance(node, ast.Call) else []
    parts = dotted(node.func if isinstance(node, ast.Call) else node) or [""]
    parts = parts[1:] if parts[0] == "pytest" else parts
    return parts[:1] in (["mark"], ["param"]) and all(_inert(a, verdict) for a in args)


def _annotation_inert(node: ast.expr, verdict: Verdict) -> bool:
    """Evaluating this annotation at ``def`` time runs no project code: what ``_inert`` accepts,
    ``X | Y`` of those, pytest's types, and subscripts of a ``_generic`` (never of the project's or
    the file's classes, whose ``__class_getitem__`` would run)."""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _annotation_inert(node.left, verdict) and _annotation_inert(node.right, verdict)
    if isinstance(node, ast.Subscript):
        return _generic(node.value, verdict) and _annotation_inert(node.slice, verdict)
    if isinstance(node, ast.Tuple | ast.List):
        return all(_annotation_inert(e, verdict) for e in node.elts)
    return isinstance(node, ast.Attribute) and _generic(node, verdict) or _inert(node, verdict)


def _generic(node: ast.expr, verdict: Verdict) -> bool:
    """A builtin generic never rebound, a name imported from typing or collections.abc and never
    bound otherwise, ``typing.X`` with ``typing`` imported alone, or pytest's."""
    parts = dotted(node) or [""]
    if len(parts) > 1:
        return (
            parts[0] == "pytest" or parts[0] == "typing" and _trusted(verdict, "typing", "typing")
        )
    origin = verdict.origins.get(parts[0])
    return parts[0] in GENERICS if origin is None else origin.startswith(GENERIC_ORIGINS)


def _bind(verdict: Verdict, name: str, origin: str = "") -> None:
    """A binding of name anywhere in the module. It keeps the import origin ``_trusted`` checks
    only while every binding of it is that same import, and any binding but its own ``class``
    statement stops it naming a class defined here."""
    verdict.origins[name] = origin if verdict.origins.get(name, origin) == origin else ""
    verdict.classes.discard(name)


def _trusted(verdict: Verdict, name: str, origin: str) -> bool:
    return verdict.origins.get(name) == origin


def _hook(verdict: Verdict, name: str, *, top: bool, by_def: bool) -> None:
    """Names pytest or Python call without the module calling them, however they are bound. A
    ``def`` of a setup hook is left to obligations, which refuses the tests it runs around."""
    if name == "pytest_plugins":
        verdict.refuse("pytest_plugins loads plugins (fixtures, hooks)", known=False)
    elif name in COUNT_HOOKS:
        verdict.refuse(f"hook {name} changes what is collected", known=False)
    elif name.startswith("pytest_") and name not in HARMLESS_HOOKS:
        verdict.refuse(f"hook {name} runs around the tests")
    elif name in (MODULE_DUNDERS if top else CLASS_DUNDERS):
        verdict.refuse(f"{name} is called implicitly (collection, subclassing, subscripting)")
    elif not by_def and name in SETUP_HOOKS | CLASS_HOOKS:
        verdict.refuse(f"setup hook {name} is bound by something other than a def")


def import_time(tree: ast.Module, is_project: Callable[[str], bool]) -> Verdict:
    """What one module that pytest imports before running its tests does at import time.

    Importing a project module runs that module's top-level code as well. That is inert for a
    blackbox case only if the runner imports the same module anyway; the caller checks this per
    test against ``verdict.imports``, once the drafted modules are known."""
    verdict = Verdict()
    _body(tree.body, verdict, is_project, top=True)
    return verdict


def _body(body: list[ast.stmt], verdict: Verdict, is_project, *, top: bool) -> None:
    for node in body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue  # docstring or a bare constant
        if isinstance(node, ast.Pass):
            continue
        if isinstance(node, ast.Import | ast.ImportFrom):
            _import(node, verdict, is_project, top=top)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            _function(node, verdict, top=top)
        elif isinstance(node, ast.ClassDef):
            _class(node, verdict, is_project, top=top)
        elif isinstance(node, ast.Assign | ast.AnnAssign):
            _assign(node, verdict, top=top)
        elif isinstance(node, ast.If) and _type_checking(node, verdict):
            continue
        else:
            compound = isinstance(node, ast.If | ast.Try | ast.With | ast.For | ast.While)
            kind = type(node).__name__
            verdict.refuse(f"module-level {kind} at import time", known=not compound)


def _import(node: ast.Import | ast.ImportFrom, verdict: Verdict, is_project, *, top: bool) -> None:
    if isinstance(node, ast.ImportFrom) and node.level:
        verdict.refuse("module-level relative import of a test helper (it runs at import time)")
        return
    if isinstance(node, ast.ImportFrom):
        names = [node.module or ""]
        bound = {a.asname or a.name: f"{node.module}.{a.name}" for a in node.names}
        origins = bound
    else:
        names = [a.name for a in node.names]
        bound = {a.asname or a.name.split(".")[0]: a.name for a in node.names}
        origins = {  # import a.b binds a; import a.b as c binds a.b
            a.asname or a.name.split(".")[0]: a.name if a.asname else a.name.split(".")[0]
            for a in node.names
        }
    if _renames_pytest(node):  # the readers know pytest's names by their spelling
        verdict.refuse("an import binds a pytest name under another name")
    for local, origin in origins.items():
        _bind(verdict, local, origin)
        _hook(verdict, local, top=top, by_def=False)
    for name in names:
        if name.split(".")[0] in SAFE_IMPORTS:
            continue
        if not is_project(name):
            verdict.refuse("module-level import of a module outside the project and stdlib")
            continue
        verdict.imports.add(name)
        verdict.project_names |= set(bound)
        if "*" in bound:
            verdict.refuse("module-level star import from the project can rebind any name")
    if not isinstance(node, ast.ImportFrom) or node.module not in ("pytest", "_pytest.mark"):
        if set(bound) & PYTEST_HEADS - {"pytest"} or bound.get("pytest", "pytest") != "pytest":
            verdict.refuse("an import binds a name this reader takes for pytest's")
        verdict.imports |= {sub for sub in bound.values() if is_project(sub)}  # from pkg import m


def _function(node: ast.FunctionDef | ast.AsyncFunctionDef, verdict: Verdict, *, top: bool):
    _bind(verdict, node.name)
    if node.name in PYTEST_HEADS:
        verdict.refuse("a def binds a name this reader takes for pytest's")
    _hook(verdict, node.name, top=top, by_def=True)
    _decorators(node.decorator_list, verdict)
    if any(_from_pytest(n) for stmt in node.body for n in ast.walk(stmt)):
        verdict.refuse("a function imports from pytest in its body (only module imports are read)")
    if _calls_outside_pytest(node.args) or (node.returns and _calls_outside_pytest(node.returns)):
        verdict.refuse("module-level default argument runs at import time")
    args = node.args
    params = [*args.posonlyargs, *args.args, *args.kwonlyargs, args.vararg, args.kwarg]
    notes = [p.annotation for p in params if p and p.annotation] + [node.returns]
    if not all(_inert(d, verdict) for d in [*args.defaults, *args.kw_defaults] if d):
        verdict.refuse("a default argument is not a literal (it is evaluated at import time)")
    if not all(_annotation_inert(n, verdict) for n in notes if n):
        verdict.refuse("an annotation is not a plain type (it is evaluated at import time)")


def _pytest_module(name: str) -> bool:
    return name.split(".")[0] in ("pytest", "_pytest")


def _from_pytest(node: ast.AST) -> bool:
    if isinstance(node, ast.ImportFrom):
        return _pytest_module(node.module or "")
    return isinstance(node, ast.Import) and any(_pytest_module(a.name) for a in node.names)


def _renames_pytest(node: ast.Import | ast.ImportFrom) -> bool:
    """``import pytest as pt``, ``from pytest import fixture as fx`` and the like."""
    if isinstance(node, ast.ImportFrom):
        names = node.names if _pytest_module(node.module or "") else []
    else:
        names = [a for a in node.names if _pytest_module(a.name)]
    return any(a.asname and a.asname != a.name for a in names)


def _decorators(decorators: list[ast.expr], verdict: Verdict) -> None:
    for d in decorators:
        if _calls_outside_pytest(d) or _foreign(d):
            verdict.refuse("module-level decorator runs at import time")
        elif isinstance(d, ast.Call) and not (
            all(k.arg for k in d.keywords)  # ** options: not read
            and all(_inert(a, verdict) for a in [*d.args, *(k.value for k in d.keywords)])
        ):
            verdict.refuse("a decorator argument is not a literal (it is evaluated at import time)")


def _foreign(decorator: ast.expr) -> bool:
    target = decorator.func if isinstance(decorator, ast.Call) else decorator
    return (dotted(target) or [""])[0] not in PYTEST_HEADS


def _class(node: ast.ClassDef, verdict: Verdict, is_project, *, top: bool) -> None:
    if node.keywords or any(_calls_outside_pytest(b) for b in node.bases):
        verdict.refuse("module-level class with a metaclass or computed base")
    if node.name in PYTEST_HEADS:
        verdict.refuse("a class binds a name this reader takes for pytest's")
    if any((dotted(b) or [""])[0] in verdict.project_names for b in node.bases):
        verdict.refuse("a class in the tests derives from a project class (__init_subclass__)")
    if not all(_safe_base(b, verdict) or _local(b, verdict) for b in node.bases):
        verdict.refuse("module-level class derives from a class defined outside the file")
    _decorators(node.decorator_list, verdict)
    _body(node.body, verdict, is_project, top=False)
    _bind(verdict, node.name)
    _hook(verdict, node.name, top=top, by_def=False)
    if top:  # a nested class is not a name the module's classes can derive from
        verdict.classes.add(node.name)


def _local(base: ast.expr, verdict: Verdict) -> bool:
    return isinstance(base, ast.Name) and base.id in verdict.classes


def _safe_base(base: ast.expr, verdict: Verdict) -> bool:
    """``object`` never rebound, or unittest's ``TestCase`` bound by its import alone."""
    parts = dotted(base)
    if parts == ["object"]:
        return "object" not in verdict.origins
    if parts == ["TestCase"]:
        return _trusted(verdict, "TestCase", "unittest.TestCase")
    return parts == ["unittest", "TestCase"] and _trusted(verdict, "unittest", "unittest")


def _assign(node: ast.Assign | ast.AnnAssign, verdict: Verdict, *, top: bool) -> None:
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    names = [t.id for t in targets if isinstance(t, ast.Name)]
    for name in names:
        _bind(verdict, name)
        _hook(verdict, name, top=top, by_def=False)
    if set(names) & PYTEST_HEADS:
        verdict.refuse("an assignment binds a name this reader takes for pytest's")
    if "pytest_plugins" in names:
        verdict.refuse("pytest_plugins loads plugins (fixtures, hooks)", known=False)
    elif len(names) != len(targets):
        verdict.refuse("module-level assignment to an attribute or item")
    elif node.value is not None and _calls_other_than_marks(node.value):
        verdict.refuse("module-level call at import time")  # pytest.importorskip(...) too
    elif not all(_inert(v, verdict) for v in (node.value, _annotation(node)) if v is not None):
        verdict.refuse("module-level value is not a literal (it can run project code)")
    if any(n.startswith("test") for n in names):  # test_b = test_a is collected too
        verdict.refuse("a test is defined by assignment", known=False)


def _annotation(node: ast.Assign | ast.AnnAssign) -> ast.expr | None:
    return node.annotation if isinstance(node, ast.AnnAssign) else None


def _type_checking(node: ast.If, verdict: Verdict) -> bool:
    """``if TYPE_CHECKING:`` holding only imports: never true at run time, as long as the flag
    (or ``typing`` for ``typing.TYPE_CHECKING``) is bound by its typing import and nothing else."""
    test = dotted(node.test) or []
    flag = test[0] if test in (["TYPE_CHECKING"], ["typing", "TYPE_CHECKING"]) else ""
    only_imports = all(isinstance(s, ast.Import | ast.ImportFrom) for s in node.body)
    trusted = bool(flag) and _trusted(verdict, flag, TYPING_NAMES[flag])
    return trusted and only_imports and not node.orelse
