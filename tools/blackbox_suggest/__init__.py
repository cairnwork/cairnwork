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

from .literals import Refused
from .obligations import collect
from .resolve import Imports, Project
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


def conftests_for(test_file: Path, project: Path) -> list[Path]:
    """conftest.py files pytest would load for test_file: its directory and each parent up to
    the project root."""
    found: list[Path] = []
    directory = test_file.parent.resolve()
    root = project.resolve()
    while True:
        candidate = directory / "conftest.py"
        if candidate.is_file():
            found.append(candidate)
        if directory == root or root not in directory.parents:
            break
        directory = directory.parent
    return list(reversed(found))


def module_env(tree: ast.Module) -> dict[str, ast.expr]:
    """Module-level names assigned exactly once (their values are evaluated only where used)."""
    seen: dict[str, ast.expr] = {}
    twice: set[str] = set()
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else []
        names = [t.id for t in targets if isinstance(t, ast.Name)]
        if isinstance(node, ast.AugAssign | ast.AnnAssign) and isinstance(node.target, ast.Name):
            twice.add(node.target.id)
        if len(targets) == 1 and len(names) == 1:
            if names[0] in seen:
                twice.add(names[0])
            seen[names[0]] = node.value  # type: ignore[union-attr]
    return {name: value for name, value in seen.items() if name not in twice}


def analyze_file(test_file: Path, project_root: Path, source_roots: list[Path]) -> list[Obligation]:
    """Every obligation of one test file, with its draft cases or refusal reason."""
    rel = test_file.resolve().relative_to(project_root.resolve()).as_posix()
    try:
        tree = _parse(test_file)
    except (SyntaxError, ValueError) as exc:
        return [Obligation(rel, rel, 1, False, f"cannot parse the test file: {exc}", [])]
    conftests = []
    for path in conftests_for(test_file, project_root):
        try:
            conftests.append(_parse(path))
        except (SyntaxError, ValueError):
            return [Obligation(rel, rel, 1, False, "cannot parse a conftest.py", [])]
    project = Project(source_roots)
    imports, env = Imports(tree), module_env(tree)
    out: list[Obligation] = []
    for item in collect(tree, rel, conftests):
        refusal, cases = item.refusal, []
        if refusal is None:
            try:
                drafts = drafts_for(item.func, item.bindings, imports, project, env)
                cases = [d.plain() for d in drafts]
            except Refused as exc:
                refusal = str(exc)
            except RecursionError:
                refusal = "the test is nested too deeply to read"
        out.append(Obligation(item.test_id, rel, item.line, item.exact, refusal, cases))
    return out


def default_roots(project_root: Path) -> list[Path]:
    return [root for root in (project_root / "src", project_root) if root.is_dir()]
