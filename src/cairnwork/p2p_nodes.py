"""The base-passing node ids a P2P plan binds, and the check that each ran on the candidate.

A P2P target is a test file or a test function; pytest expands it into node ids on whatever
commit it runs on. The candidate controls the code a parametrisation is built from, so a target
that passed on the base can run fewer node ids on the candidate (a broken case removed from a
list) and still exit 0. The plan therefore binds the node ids themselves: each one the base
passed and a target covers must have run and passed on the candidate; one that did not run is not
a pass.

A test file the test commit changes stays a P2P file. Its tests that the test commit adds,
changes or removes (``changed_tests``) are left to the author's verifiers; every
other test the base passed in it is bound by function, never by the whole file, which on the
candidate also holds the test commit's tests.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from cairnwork.deferred_tests import _test_functions
from cairnwork.verifiers import VerifierRun


def changed_tests(
    repo: Path, base: str, test_commit: str, paths: tuple[str, ...]
) -> frozenset[str]:
    """``deferred_tests.new_tests`` plus the test functions the test commit removes: the base
    outcome of these says nothing about the candidate. A test changed only in a way the AST
    comparison does not see (see ``new_tests``) is not in it, so it stays a P2P test."""
    found = set()
    for path in paths:
        if path.endswith(".py"):
            before = _test_functions(repo, f"{base}:{path}", present=True)  # on both commits
            after = _test_functions(repo, f"{test_commit}:{path}", present=True)
            found |= {f"{path}::{n}" for n in before.keys() | after.keys()
                      if before.get(n) != after.get(n)}  # fmt: skip
    return frozenset(found)


def _function(nodeid: str) -> str:
    return nodeid.split("[", 1)[0]


def passing_targets(
    outcomes: dict[str, str],
    changed_files: frozenset[str] = frozenset(),
    changed: frozenset[str] = frozenset(),
) -> tuple[tuple[str, ...], tuple[str, ...], int]:
    """(targets, bound node ids, node ids left to the author's verifiers). A file whose every
    test passed on the base, and that the test commit does not change, is a target by itself; in
    any other file only the test functions (all parametrisations passed) are. ``changed`` holds
    the ``path::function`` ids the test commit changed, which are never targets. Everything keeps
    the order of ``outcomes`` (the order the probe ran them, #287), never sorted: pytest runs node
    ids in the order given, and a test may depend on the tests before it."""
    by_file: dict[str, dict[str, bool]] = {}
    left = 0
    for nodeid, outcome in outcomes.items():
        if _function(nodeid) in changed:
            left += outcome == "passed"
            continue
        funcs = by_file.setdefault(nodeid.split("::", 1)[0], {})
        funcs[_function(nodeid)] = funcs.get(_function(nodeid), True) and outcome == "passed"
    targets: list[str] = []
    for path, funcs in by_file.items():
        if all(funcs.values()) and path not in changed_files:
            targets.append(path)
        else:
            targets.extend(f for f, ok in funcs.items() if ok)
    covered = set(targets)
    nodes = tuple(n for n in outcomes if _function(n) in covered or n.split("::", 1)[0] in covered)
    return tuple(targets), nodes, left


def require_nodes(nodes: tuple[str, ...], run: VerifierRun) -> tuple[VerifierRun, list[str]]:
    """``run`` as Core may see it, and the bound node ids it lacks: a passed run that did not
    run and pass every bound node id is not a pass, and not a plain (re-runnable) failure."""
    if not run.observation.passed:
        return run, []
    missing = [n for n in nodes if run.outcomes.get(n) != "passed"]
    if not missing:
        return run, []
    why = f"{len(missing)} base-passing test(s) did not run on the candidate"
    observation = replace(run.observation, passed=False)
    return replace(run, observation=observation, plain_failure=False, why=why), missing
