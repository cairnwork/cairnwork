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

from .aliases import resolve_aliases
from .config import pytest_config
from .literals import Refused
from .obligations import Scope, collect
from .resolve import Imports, Project, module_env
from .scope import import_time
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
    return resolve_aliases(ast.parse(path.read_bytes(), filename=str(path)))


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
    out: list[Obligation] = []
    for item in collect(tree, rel, conftests, scope, inits):
        refusal, cases = item.refusal, []
        if refusal is None:
            try:
                drafts = drafts_for(item.func, item.bindings, imports, project, env)
                cases = [d.plain() for d in drafts]
                refusal = _unimported(cases, scope, project)
            except Refused as exc:
                refusal = str(exc)
            except RecursionError:
                refusal = "the test is nested too deeply to read"
        out.append(Obligation(item.test_id, rel, item.line, item.exact, refusal, cases))
    return out


def default_roots(project_root: Path) -> list[Path]:
    return [root for root in (project_root / "src", project_root) if root.is_dir()]
