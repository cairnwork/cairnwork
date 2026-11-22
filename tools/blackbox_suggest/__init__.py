"""Suggest blackbox declarations from a project's tests, and measure protected coverage (#303).

Static only: test files, conftest files and the project's modules are parsed with ``ast``; nothing
is imported or run, and expected values come from the tests' own literals, never from running the
candidate. Each collected test (each parametrized case) is one acceptance obligation. An
obligation is *protected* when every check in it can be declared as a blackbox case of a kind the
cairnwork version supports (``kinds``); otherwise it carries the reason it cannot. The drafts are
suggestions for the owner to review, not a declaration to trust blindly: see
docs/BLACKBOX_COVERAGE.md.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import import_dirs, pytest_config, recursion_skips, strict_ids
from .ids import distinct_ids
from .literals import Refused
from .obligations import Scope, collect
from .resolve import Imports, Project, binds_module_hook, module_env
from .scope import import_time
from .shadows import clashes, stdlib_shadows, walk
from .shapes import drafts_for

KINDS_NOW = frozenset({"function"})  # what cairnwork.blackbox declares at this version
ALL_KINDS = frozenset({"function", "construct", "method", "sequence", "raises", "float"})


@dataclass
class Obligation:
    test_id: str
    file: str
    line: int
    exact: bool  # False: the number of cases of this test could not be counted statically
    refusal: str | None
    cases: list[dict[str, Any]]

    @property
    def needs(self) -> set[str]:
        return {kind for case in self.cases for kind in case["needs"]}

    def protected(self, kinds: frozenset[str] | set[str]) -> bool:
        return self.refusal is None and bool(self.cases) and self.needs <= set(kinds)

    def reason(self, kinds: frozenset[str] | set[str]) -> str | None:
        if self.refusal is not None:
            return self.refusal
        missing = self.needs - set(kinds)
        return f"needs {', '.join(sorted(missing))} (not declarable here)" if missing else None

    def plain(self, kinds: frozenset[str] | set[str]) -> dict[str, Any]:
        return {
            "test_id": self.test_id,
            "file": self.file,
            "line": self.line,
            "exact": self.exact,
            "protected": self.protected(kinds),
            "reason": self.reason(kinds),
            "needs": sorted(self.needs),
            "cases": self.cases,
        }


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_bytes(), filename=str(path))


def conftests_for(test_file: Path, project: Path, name: str = "conftest.py") -> list[Path]:
    """Files called name that pytest imports for test_file (conftest.py, or a test package's
    __init__.py): its directory and each parent up to the project root."""
    found: list[Path] = []
    directory = test_file.parent.resolve()
    root = project.resolve()
    while True:
        candidate = directory / name
        if candidate.is_file():
            found.append(candidate)
        if directory == root or root not in directory.parents:
            break
        directory = directory.parent
    return list(reversed(found))


def _scope(test_file: Path, root: Path, project: Project, modules: list[ast.Module]) -> Scope:
    """What the configuration and every module imported around the test file decide."""
    reason, known, usefixtures = pytest_config(test_file.parent, root)
    walked = walk(root, recursion_skips(test_file.parent, root))
    dirs = None
    if walked is not None:
        dirs = [*import_dirs(test_file.parent, root), *project.roots, *walked.base_dirs]
    shadows = None if dirs is None else stdlib_shadows(dirs)
    twice = None if dirs is None else clashes(dirs)
    if shadows is None or twice is None:
        reason = (
            reason or "a directory pytest may collect or import from cannot be read or is a link"
        )
    elif shadows:
        reason = reason or "a file on pytest's import path takes a standard-library module name"
    elif twice:  # which one an import loads depends on the path order (#370)
        reason = reason or "a module name is defined in two places pytest may import from"
    if walked is not None and walked.doctests:
        reason = reason or "pytest collects a text file as doctests, which run unread code"
    tests_top = test_file.resolve().parent

    def is_project(name: str) -> bool:
        path = project.module_file(name)
        if path is None:
            return False
        path = path.resolve()  # a test helper that happens to be importable is not the project
        return tests_top not in path.parents and not {"tests", "test"} & set(path.parts)

    imported: set[str] = set()
    for module in modules:
        verdict = import_time(module, is_project)
        reason = reason or verdict.reason
        known = known and verdict.known
        imported |= verdict.imports
    return Scope(reason, known, usefixtures, imported)


def _unimported(cases: list[dict[str, Any]], scope: Scope, project: Project) -> str | None:
    """Refusal when pytest imports a project module the blackbox runner would not: its
    import-time code (``pkg.core.SCALE = 10``) then changes what the test sees."""
    drafted = {case["target"]["module"] for case in cases}
    if scope.imports - project.import_closure(drafted):
        return "the tests import a project module the blackbox would not import"
    return None


def _module_hooks(cases: list[dict[str, Any]], project: Project) -> str | None:
    """Refusal when a project module that the drafted targets import binds a module
    ``__getattr__`` or ``__dir__``: a lookup of a name it lacks (``from pkg import core``, or
    pytest probing the objects in a test module) runs that module's code. Called after
    ``_unimported``, so this also covers every project module pytest imports for the test. Any
    import statement is followed, and a module with no single readable source refuses."""
    drafted = {case["target"]["module"] for case in cases}
    for module in sorted(project.import_closure(drafted, anywhere=True)):
        tree = project.tree(module)
        if tree is None or project.files(module) != [project.module_file(module)]:
            return f"{module} has no single source file this reader can read"
        if binds_module_hook(tree):
            return f"{module} binds a module-level __getattr__ or __dir__"
    return None


def analyze_file(test_file: Path, project_root: Path, source_roots: list[Path]) -> list[Obligation]:
    """Every obligation of one test file, with its draft cases or refusal reason."""
    rel = test_file.resolve().relative_to(project_root.resolve()).as_posix()
    try:
        tree = _parse(test_file)
    except (SyntaxError, ValueError) as exc:
        return [Obligation(rel, rel, 1, False, f"cannot parse the test file: {exc}", [])]
    conftests, inits = [], []
    try:
        conftests = [_parse(p) for p in conftests_for(test_file, project_root)]
        inits = [_parse(p) for p in conftests_for(test_file, project_root, "__init__.py")]
    except (SyntaxError, ValueError):
        return [Obligation(rel, rel, 1, False, "cannot parse a conftest.py or __init__.py", [])]
    project = Project(source_roots)
    scope = _scope(test_file, project_root, project, [*inits, *conftests, tree])
    imports, env = Imports(tree), module_env(tree)
    ids = None  # pytest stops collecting the file on a repeated id (#370)
    if strict_ids(test_file.parent, project_root) and not distinct_ids([tree, *conftests]):
        ids = "pytest config makes a repeated parametrize id an error; ids here may repeat"
    out: list[Obligation] = []
    for item in collect(tree, rel, conftests, scope, inits):
        refusal, cases = item.refusal or ids, []
        if refusal is None:
            try:
                drafts = drafts_for(item.func, item.bindings, imports, project, env)
                cases = [d.plain() for d in drafts]
                refusal = _unimported(cases, scope, project)
                refusal = refusal or _module_hooks(cases, project)
            except Refused as exc:
                refusal = str(exc)
            except RecursionError:
                refusal = "the test is nested too deeply to read"
        out.append(Obligation(item.test_id, rel, item.line, item.exact, refusal, cases))
    return out


def default_roots(project_root: Path) -> list[Path]:
    return [root for root in (project_root / "src", project_root) if root.is_dir()]
