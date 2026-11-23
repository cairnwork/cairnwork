"""#974: the tests pytest collects as new in the test commit must be tests the reader can see.

``deferred_tests.new_tests`` reads names and the AST. Where it misses a new test (a unittest base
reached through an alias, attribute or subscript, a default ``runTest``, a doctest the base's
settings turn on) and a visible new test sits beside it, the hidden one was never required to run:
a false green. This check asks pytest itself. One collect-only run on the base and one on the test
commit (same sandbox, ini and guard as every verifier run, collect_reader.py) over the test
commit's Python files: an id collected only on the test commit, parametrisation stripped, that
``new_tests`` did not return is refused, DEFERRED_TESTS_UNREADABLE.

It only adds refusals. A collector error in a file leaves that file to the static rule. A test
commit run with no usable record (timeout, crash, no report) is refused
DEFERRED_TESTS_COLLECT_FAILED when a base probe collects normally (the candidate must not be able
to turn the check off by failing the run; a run broken everywhere stays UNCOMPARABLE, #804);
more than MAX_FILES files are collected in batches; only an unreadable base run leaves the old
ids unknown and the static rule in place;
the receipt receiver re-derives the set statically without running pytest, so a test commit this
check accepts is judged the same there. A test whose body changes under an id the base already
collects is not seen by ids: the AST comparison still decides it.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from cairnwork import base_fixture_names
from cairnwork.collect_reader import Collected, read_collected
from cairnwork.conftest_check import conftest_of
from cairnwork.deferred_tests import TestCommitRejected
from cairnwork.gitcmd import GitCommandError, run_git
from cairnwork.node_file import file_part
from cairnwork.p2p_function import _without_case
from cairnwork.verifier_spec import VerifierSpec
from cairnwork.verifiers import run_verifiers

MAX_FILES = 50  # files per collect-only run; more changed files are collected in batches
NONE = Collected()


def check_with_base(ctx, task, paths: tuple[str, ...], run_id: str, targets) -> None:
    """The base fixture names check (#986), then this one (#974): one call for the judge."""
    base_fixture_names.check_with_base(ctx, task, paths, run_id)
    check(ctx, task, paths, targets, f"{run_id}c")


def check(ctx, task, paths: tuple[str, ...], targets, run_id: str) -> None:
    """Refuse a new collected test that ``targets`` (``new_tests``) does not hold."""
    skip = set(conftest_of(paths))
    files = tuple(p for p in paths if p.endswith(".py") and p not in skip)
    if not files:
        return
    after = _collect_all(ctx, task.test_commit, files, f"{run_id}t")
    on_base = tuple(f for f in files if _exists(task, f))
    if after is None:
        # no usable record. If a base probe reads, the failure is specific to the test commit
        # (the run may be the lever): refuse. If the probe fails too, the run itself is broken
        # here (e.g. a table plugin that fails to load): the base run reports UNCOMPARABLE (#804)
        if _collect_all(ctx, task.base_commit, _probe_files(task, on_base), f"{run_id}p") is None:
            return
        raise TestCommitRejected(
            "DEFERRED_TESTS_COLLECT_FAILED",
            "pytest's collect-only run on the test commit gave no usable record (timeout,"
            " crash or no report), so Cairnwork cannot tell which tests it adds; rerun the task",
        )
    before = _collect_all(ctx, task.base_commit, on_base, f"{run_id}b") if on_base else NONE
    if before is None:  # the base's own run failed: the old ids are unknown, the static rule stays
        return
    unknown = {*after.error_files, *after.skipped_files, *before.error_files, *before.skipped_files}
    old = {_without_case(n) for n in before.ids}
    seen = set(targets)
    new = {_without_case(n) for n in after.ids} - old - seen
    unseen = [n for n in new if file_part(n) not in unknown]
    if unseen:  # counts and fixed words only: a node id is candidate-controlled text
        raise TestCommitRejected(
            "DEFERRED_TESTS_UNREADABLE",
            f"pytest collects {len(unseen)} new test(s) in the changed files that the reader"
            " cannot see; Cairnwork cannot tell which tests the commit adds, so it cannot check"
            " they ran; write them as plain Test* classes or top-level test functions",
        )


def _collect_all(ctx, commit: str, files: tuple[str, ...], run_id: str) -> Collected | None:
    """``_collect`` in batches of MAX_FILES, merged; None when any batch has no usable record."""
    parts = [
        _collect(ctx, commit, files[i : i + MAX_FILES], f"{run_id}{i // MAX_FILES}")
        for i in range(0, len(files), MAX_FILES)
    ]
    if any(part is None for part in parts):
        return None
    got: list[Collected] = [p for p in parts if p is not None]
    return Collected(
        tuple(sorted({n for p in got for n in p.ids})),
        tuple(sorted({n for p in got for n in p.error_files})),
        tuple(sorted({n for p in got for n in p.skipped_files})),
    )


def _collect(ctx, commit: str, files: tuple[str, ...], run_id: str) -> Collected | None:
    """The collected ids on ``commit``, or None when the run cannot be read (any fault)."""
    try:
        spec = VerifierSpec("collected-974", files, collect_only=True)
        runs = run_verifiers(ctx, commit, (spec,), run_id)
    except Exception:  # noqa: BLE001 - a fault leaves the static rule alone
        return None
    try:
        got = read_collected(runs[0]) if len(runs) == 1 else None
    finally:
        for run in runs:  # evidence for this check only, not for Core
            shutil.rmtree(Path(run.log_path).parent, ignore_errors=True)
    return None if got is None or got.unusable else got


def _probe_files(task, on_base: tuple[str, ...]) -> tuple[str, ...]:
    """The changed files that exist on base, else one test file of the base (all files are new)."""
    if on_base:
        return on_base
    try:
        listing = run_git(task.repo, "ls-tree", "-r", "--name-only", task.base_commit)
    except GitCommandError:
        return ()
    names = [n for n in listing.splitlines() if n.endswith(".py") and "test" in Path(n).name]
    return tuple(names[:1])


def _exists(task, path: str) -> bool:
    try:
        run_git(task.repo, "cat-file", "-e", f"{task.base_commit}:{path}")
    except GitCommandError:
        return False
    return True
