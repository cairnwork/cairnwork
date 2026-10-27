"""What runs when pytest imports a test module, a conftest or a test package's ``__init__``.

Any code that runs there can change what every test sees, and a blackbox case repeats only the
call. So this is an allow-list, not a list of known dangers: a module keeps its tests declarable
only if every top-level statement is one of a few inert shapes (docstrings; imports of pytest,
unittest, the standard library or the project; literal assignments; ``def`` and ``class`` whose
decorators, defaults and bases call nothing but pytest; ``if TYPE_CHECKING:`` imports). Anything
else refuses every test in its scope, and statements that may define or collect tests mark the
count inexact. Nothing is executed: the modules are parsed with ``ast``.
"""

from __future__ import annotations

import ast
import sys
from collections.abc import Callable

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


class Verdict:
    """The first reason found (None: inert), whether the count stays known, and the project
    modules imported (each one's import-time code runs too: see ``imports``)."""

    def __init__(self) -> None:
        self.reason: str | None = None
        self.known = True
        self.imports: set[str] = set()
        self.project_names: set[str] = set()  # local names bound by those imports

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
            _import(node, verdict, is_project)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            _function(node, verdict, top=top)
        elif isinstance(node, ast.ClassDef):
            _class(node, verdict, is_project)
        elif isinstance(node, ast.Assign | ast.AnnAssign):
            _assign(node, verdict)
        elif isinstance(node, ast.If) and _type_checking(node):
            continue
        else:
            compound = isinstance(node, ast.If | ast.Try | ast.With | ast.For | ast.While)
            kind = type(node).__name__
            verdict.refuse(f"module-level {kind} at import time", known=not compound)


def _import(node: ast.Import | ast.ImportFrom, verdict: Verdict, is_project) -> None:
    if isinstance(node, ast.ImportFrom) and node.level:
        verdict.refuse("module-level relative import of a test helper (it runs at import time)")
        return
    if isinstance(node, ast.ImportFrom):
        names = [node.module or ""]
        bound = {a.asname or a.name: f"{node.module}.{a.name}" for a in node.names}
    else:
        names = [a.name for a in node.names]
        bound = {a.asname or a.name.split(".")[0]: a.name for a in node.names}
    for name in names:
        if name.split(".")[0] in SAFE_IMPORTS:
            continue
        if not is_project(name):
            verdict.refuse("module-level import of a module outside the project and stdlib")
            continue
        verdict.imports.add(name)
        verdict.project_names |= set(bound)
    if not isinstance(node, ast.ImportFrom) or node.module not in ("pytest", "_pytest.mark"):
        if set(bound) & PYTEST_HEADS - {"pytest"} or bound.get("pytest", "pytest") != "pytest":
            verdict.refuse("an import binds a name this reader takes for pytest's")
        verdict.imports |= {sub for sub in bound.values() if is_project(sub)}  # from pkg import m


def _function(node: ast.FunctionDef | ast.AsyncFunctionDef, verdict: Verdict, *, top: bool):
    if node.name in PYTEST_HEADS:
        verdict.refuse("a def binds a name this reader takes for pytest's")
    if top and node.name in COUNT_HOOKS:
        verdict.refuse(f"hook {node.name} changes what is collected", known=False)
    elif top and node.name.startswith("pytest_") and node.name not in HARMLESS_HOOKS:
        verdict.refuse(f"hook {node.name} runs around the tests")
    if any(_calls_outside_pytest(d) or _foreign(d) for d in node.decorator_list):
        verdict.refuse("module-level decorator runs at import time")
    if _calls_outside_pytest(node.args) or (node.returns and _calls_outside_pytest(node.returns)):
        verdict.refuse("module-level default argument runs at import time")


def _foreign(decorator: ast.expr) -> bool:
    target = decorator.func if isinstance(decorator, ast.Call) else decorator
    return (dotted(target) or [""])[0] not in PYTEST_HEADS


def _class(node: ast.ClassDef, verdict: Verdict, is_project) -> None:
    if node.keywords or any(_calls_outside_pytest(b) for b in node.bases):
        verdict.refuse("module-level class with a metaclass or computed base")
    if node.name in PYTEST_HEADS:
        verdict.refuse("a class binds a name this reader takes for pytest's")
    if any((dotted(b) or [""])[0] in verdict.project_names for b in node.bases):
        verdict.refuse("a class in the tests derives from a project class (__init_subclass__)")
    if any(_calls_outside_pytest(d) or _foreign(d) for d in node.decorator_list):
        verdict.refuse("module-level decorator runs at import time")
    _body(node.body, verdict, is_project, top=False)


def _assign(node: ast.Assign | ast.AnnAssign, verdict: Verdict) -> None:
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    names = [t.id for t in targets if isinstance(t, ast.Name)]
    if set(names) & PYTEST_HEADS:
        verdict.refuse("an assignment binds a name this reader takes for pytest's")
    if "pytest_plugins" in names:
        verdict.refuse("pytest_plugins loads plugins (fixtures, hooks)", known=False)
    elif len(names) != len(targets):
        verdict.refuse("module-level assignment to an attribute or item")
    elif node.value is not None and _calls_other_than_marks(node.value):
        verdict.refuse("module-level call at import time")  # pytest.importorskip(...) too
    if any(n.startswith("test") for n in names):  # test_b = test_a is collected too
        verdict.refuse("a test is defined by assignment", known=False)


def _type_checking(node: ast.If) -> bool:
    """``if TYPE_CHECKING:`` holding only imports: never true at run time."""
    test = (dotted(node.test) or [""])[-1]
    only_imports = all(isinstance(s, ast.Import | ast.ImportFrom) for s in node.body)
    return test == "TYPE_CHECKING" and only_imports and not node.orelse
