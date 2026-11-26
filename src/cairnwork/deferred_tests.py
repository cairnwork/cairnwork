"""Deferred tests: the tests that prove a fix are applied only after the worker is done.
A task may name a ``test_commit``: a commit whose only parent is the task's base and which adds
or changes files under ``tests/`` only (a ``conftest.py`` only as an additive fixture change,
conftest_check.py and p2p_conftest.py, #798; regular files only). The worker never gets it: its
attempt clone holds the base. Cairnwork then:

1. runs the verifiers on the test commit itself (base + tests) and requires a real failure there
   (base_comparison): passing tests prove nothing (REFUSED); collection errors, timeouts, missing
   sandbox/report, oversized logs, lingering processes, changed checkouts or forged bytecode make
   the base unjudgeable (UNCOMPARABLE, #234);
2. lays the test files over the worker's candidate in a private index and commits the result; the
   verifiers run that commit, Core notarises it and a PR carries fix and tests together.

So the worker cannot read or weaken the tests (like SWE-bench applying tests after the patch)
unless it reaches the commit elsewhere. The Issue runner never tells it the id; deferred_store.py
is on no remote, though a public branch could expose it to a worker.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from cairnwork import deferred_attached, gitprobe, survivor_argv
from cairnwork.base_teeth import base_teeth
from cairnwork.deferred_nodes import hidden_tests, local_names, target_ids, test_functions
from cairnwork.deferred_overlay import (  # noqa: F401  (re-exported: the one place importers look)
    _REGULAR,
    MAX_DELETED_TEST_PATHS,
    OVERLAY_RULE,
    TestCommitRejected,
    TestFiles,
    author_reason,
    deleted_of,
    overlay_tree,
)
from cairnwork.gitcmd import OID, GitCommandError, commit_present, run_git
from cairnwork.issue_task import test_target
from cairnwork.node_ids import file_part
from cairnwork.p2p_function import _without_case as _nodeid_without_case
from cairnwork.provenance import trailers
from cairnwork.reasons import COLLECT_HINT, _names, no_tests_hint, skip_hint
from cairnwork.refusal_types import _shown_detail
from cairnwork.test_commit_paths import case_variant_clash, out_of_scope  # noqa: F401
from cairnwork.workspace import ATTEMPT_REF_PREFIX
from cairnwork_protocol.ids import MAX_ID_BYTES

_IDENTITY = ("-c", "user.name=Cairnwork", "-c", "user.email=cairnwork@localhost")
_EMPTY = ast.Module(body=[], type_ignores=[])  # a file that is not there (new, or deleted)


def never_seen(repo: Path, test_commit: str) -> None:
    """Before the worker starts: the test commit must not be on this machine yet. A retry
    or a sibling task that already fetched it would have left it (and its logs) where the
    worker can read them, so such a task needs a fresh test commit."""
    try:  # only a silent exit 1 is "no such object"; anything else refuses (#1011)
        found = run_git(repo, "rev-parse", "--verify", "--quiet", f"{test_commit}^{{commit}}",
                        ok_codes=(0, 1))  # fmt: skip
    except GitCommandError as exc:
        raise TestCommitRejected("TEST_COMMIT_EXPOSURE_UNKNOWN", "git could not answer") from exc
    if not found.strip():
        return
    raise TestCommitRejected("TEST_COMMIT_ALREADY_EXPOSED", "push the tests as a new commit")


def ensure_test_commit(
    repo: Path, test_commit: str, store=None, *, origin_url=None, allowed_protocols=("https", "ssh")
) -> None:
    """Fetch the test commit into repo only now, after the worker is done: until then
    nothing on this machine holds the tests (the Issue runner reads its parent via the
    GitHub API, or from the private store). With a store (deferred_store.py, Issue #119)
    the commit comes from its bundle and never from a remote the worker could also reach;
    a ``required`` store refuses a commit that was not imported instead of falling back.
    It does so before looking at repo: a commit that is already there but never went
    through the store reached the machine by another route (a fetch from origin, a push)
    and must not pass as "bundle" (#189); one the store holds is fetched from its bundle.
    Without a required store, a locally present commit is accepted without a configured
    verifier-owned origin; Git operations ignore replacement refs (#1312)."""
    if not OID.fullmatch(test_commit):
        raise TestCommitRejected("TEST_COMMIT_INVALID", "not a 40-hex commit id")
    if store is not None and store.required:  # provenance first, presence never suffices
        if not store.has(test_commit):
            raise TestCommitRejected("TEST_COMMIT_NOT_IMPORTED", test_commit)
        store.fetch_into(repo, test_commit)
        return
    if commit_present(repo, test_commit):
        return
    if store is not None and store.has(test_commit):
        store.fetch_into(repo, test_commit)
        return
    try:
        gitprobe.fetch_to_supplied(repo, origin_url, test_commit, protocols=allowed_protocols)
    except gitprobe.TestCommitUnavailable as exc:
        raise TestCommitRejected("TEST_COMMIT_UNREADABLE", "origin did not provide it") from exc


def check_roots_on_base(repo: Path, base: str, roots: tuple[str, ...]) -> None:
    """Every part of each configured root that exists on the base must be a real directory:
    a symlink or file there could point the tests (or verifier targets) outside the root."""
    for root in roots:
        parts = root.split("/")
        found_parts = []
        for i, part in enumerate(parts):
            try:  # the parent's entries: a case variant (Tests/) may alias the root on some disks
                where = ["--", "/".join(found_parts) + "/"] if i else []
                raw = run_git(repo, "ls-tree", "-z", base, *where)
            except GitCommandError as exc:
                raise TestCommitRejected("TEST_ROOT_UNREADABLE", str(exc)) from exc
            found = [
                e
                for e in raw.split("\0")
                if e.rsplit("\t", 1)[-1].rsplit("/", 1)[-1].casefold() == part.casefold()
            ]
            if not found:
                break  # absent on the base: nothing to hide behind (a test commit may add it)
            if len(found) > 1 or not found[0].startswith("040000 tree "):
                raise TestCommitRejected(
                    "TEST_ROOT_NOT_A_DIRECTORY", f"{parts[: i + 1]} on the base"
                )
            found_parts.append(found[0].rsplit("\t", 1)[-1].rsplit("/", 1)[-1])


def test_paths(
    repo: Path, base: str, test_commit: str, roots: tuple[str, ...] = ("tests",)
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """The files the test commit adds or changes, and those it deletes (#798; a move is both);
    refuse anything but plain test files, and any
    path that can never pass (#656): one over Core's text limit (receipts and the acceptance spec
    carry each path), or a Python file no verifier target can name (``untargeted`` requires it)."""
    try:
        parents = run_git(repo, "rev-list", "--parents", "-n", "1", test_commit).split()
        raw = run_git(
            repo, "diff-tree", "-r", "-z", "--no-renames", "--no-commit-id", "--raw",
            "--ignore-submodules=none", base, test_commit,
        )  # fmt: skip
    except GitCommandError as exc:
        raise TestCommitRejected("TEST_COMMIT_UNREADABLE", str(exc)) from exc
    if parents[1:] != [base]:
        raise TestCommitRejected("TEST_COMMIT_NOT_ON_BASE", "its only parent must be the base")
    fields = raw.split("\0")
    paths, deleted, target = [], [], test_target(roots)
    for meta, path in zip(fields[0::2], fields[1::2], strict=False):
        old_mode, new_mode, status = meta.split()[0][1:], meta.split()[1], meta.split()[4]
        if (
            status not in ("A", "M", "D")
            or (old_mode if status == "D" else new_mode) not in _REGULAR
            or not path.startswith(tuple(f"{r}/" for r in roots))
            or out_of_scope(path, status)
        ):
            raise TestCommitRejected("TEST_COMMIT_SCOPE", f"{status} {new_mode} {path}")
        if len(path.encode("utf-8")) > MAX_ID_BYTES:
            raise TestCommitRejected(
                "TEST_COMMIT_SCOPE", f"path over {MAX_ID_BYTES} UTF-8 bytes: {path[:80]}..."
            )
        if status != "D" and path.endswith(".py") and not target.fullmatch(path):
            raise TestCommitRejected(  # data and deleted files need no target
                "TEST_COMMIT_SCOPE", f"no verifier target can name {path!r} (names: [A-Za-z0-9_-])"
            )
        (deleted if status == "D" else paths).append(path)
    if not paths:
        raise TestCommitRejected("TEST_COMMIT_EMPTY")
    # #488: right code and message; the test commit is still consumed
    if clash := case_variant_clash(repo, base, paths, deleted):
        raise TestCommitRejected("TEST_COMMIT_SCOPE", clash)
    if len(deleted) > MAX_DELETED_TEST_PATHS:  # the receipt's binding refuses more (#870)
        raise TestCommitRejected(
            "TEST_COMMIT_SCOPE",
            f"{len(deleted)} deleted test files (at most {MAX_DELETED_TEST_PATHS})",
        )
    return tuple(paths), tuple(deleted)


def new_tests(repo: Path, base: str, test_commit: str, paths: tuple[str, ...]) -> frozenset[str]:
    """Node ids (``path::name`` or ``path::Class::name``) of the test functions the test
    commit adds or changes: only these may count as teeth, not tests in the same file
    that already failed on the base for another reason (e.g. the environment).

    Top-level ``test*`` functions and ``test*`` methods of top-level ``Test*`` and TestCase-subclass
    classes, including
    those a class inherits from one defined earlier in the file (deferred_nodes.py), compared by
    AST (decorators included, line numbers not). A new or changed test in a class pytest does not
    collect (an ``__init__`` or ``__new__``) is refused, DEFERRED_TESTS_NOT_COLLECTED: no verifier
    can run it (#660). Not seen: tests changed only through a fixture, helper or module constant,
    classes inherited from another module, custom python_functions/python_classes.
    #1833: main's set is kept whole; to it is added an id only the wider reading (a class with
    an imported base also counts as a test case) gives on the test commit and whose digest
    differs from that wider reading of the base, so an unchanged imported-base class is never a
    new test (deferred_nodes.target_ids). Such a test
    commit is refused (no teeth found, or the base run's DEFERRED_TESTS_NOT_RUN) when the file has
    no other new test; one beside a visible new test is not. A changed test inside a module-level
    block or a nested test class is refused, DEFERRED_TESTS_UNREADABLE (#925)."""
    found, local = set(), local_names(repo, test_commit)  # #925
    for path in paths:
        if not path.endswith(".py"):
            continue
        tree = _tree(repo, f"{test_commit}:{path}") or _EMPTY
        old = _tree(repo, f"{base}:{path}") or _EMPTY
        if hidden_tests(tree, local) != hidden_tests(old, local):  # #925
            raise TestCommitRejected(
                "DEFERRED_TESTS_UNREADABLE",
                f"{path}: a test inside a module-level if/try/with/loop block, a nested test class,"
                " a star import, a class-level global, a rebound test name or an imported"
                " TestCase (local, renamed or relative) changed; Cairnwork cannot tell which tests"
                " it adds. Define it with a top-level def test_* or in a top-level Test* class",
            )
        deferred_attached.refuse_attached(path, tree, old)  # #994
        ids, uncollected = target_ids(old, tree)
        for node_id in ids:
            if "::" in node_id and node_id.split("::", 1)[0] in uncollected:
                raise TestCommitRejected(
                    "DEFERRED_TESTS_NOT_COLLECTED",
                    "1 new test: pytest does not collect a test class with __init__"
                    " or __new__, so no verifier runs it",  # #1186: no name
                )
            found.add(f"{path}::{node_id}")
    return frozenset(found)


def untargeted(
    paths: tuple[str, ...], nodes: Iterable[str], targets: Iterable[str]
) -> tuple[str, ...]:
    """The test commit's Python files (``paths``) that the verifiers' ``targets`` do not cover. A
    file is covered when a target names it whole, or when it has new or changed tests (``nodes``,
    from new_tests) and a target names each of them or a class holding it. A file with no such test
    (a helper, ``__init__.py``, a fixture-only change) must be named whole. Other files (data)
    cannot be pytest targets and are not required. A receipt binds every test path, so each one's
    changes must have run (DEFERRED_TESTS_NOT_TARGETED; the receiver applies the same rule,
    cairnwork_attest.verify_coverage). Tests a file keeps unchanged are P2P's (p2p.py). A target
    naming one parametrized case (``::test_y[case]``) names its test ``test_y`` (#714)."""
    named, missing = frozenset(map(_without_case, targets)), []  # a case names its test
    nodes = list(map(_without_case, nodes))  # the same on both sides (the receiver too)
    for path in (path for path in paths if path.endswith(".py") and path not in named):
        mine = [node for node in nodes if file_part(node) == path]
        if not mine or not all(_named(node, named) for node in mine):
            missing.append(path)
    return tuple(missing)


def _without_case(node_id: str, all_parts: bool = False) -> str:
    """``path::name[case]`` -> ``path::name``; a ``[`` in the path part is left alone, so a
    target the producer's grammar refuses (``tests/test_x.py[x]``) names no file here either."""
    return _nodeid_without_case(node_id, all_parts=all_parts)


def _named(node: str, targets: frozenset[str]) -> bool:
    parts = node.split("::")
    return any("::".join(parts[:i]) in targets for i in range(2, len(parts) + 1))


def _test_functions(
    repo: Path, blob: str, *, present: bool = False, imported: bool = True
) -> dict[str, str]:
    """``present``: the file is known to exist, so a failed read is a refusal, not a new file.
    ``imported=False``: read an imported base as main did (see ``test_functions``)."""
    tree = _tree(repo, blob, present=present)
    return test_functions(tree, imported=imported)[0] if tree is not None else {}


def _tree(repo: Path, blob: str, *, present: bool = False) -> ast.Module | None:
    """The parsed file, or None for a file that is not there (a new file) unless ``present``."""
    try:
        source = run_git(repo, "show", blob)
    except GitCommandError as exc:
        if present:
            raise TestCommitRejected("TEST_COMMIT_UNREADABLE", str(exc)) from exc
        rev, path = blob.split(":", 1)
        try:
            exists = run_git(repo, "ls-tree", "--name-only", rev, "--", path)
        except GitCommandError as check_exc:
            raise TestCommitRejected("TEST_COMMIT_UNREADABLE", str(check_exc)) from check_exc
        if exists:
            raise TestCommitRejected("TEST_COMMIT_UNREADABLE", str(exc)) from exc
        return None  # a new file
    try:
        return ast.parse(source)
    except (SyntaxError, ValueError, RecursionError, MemoryError) as exc:
        raise TestCommitRejected("TEST_COMMIT_UNPARSABLE", blob) from exc


BASE_COMPARE_RULE = "base-compare-v1"  # how base runs map to a Comparison (#234)


@dataclass(frozen=True)
class Comparison:
    """The deferred tests on the base (#234): TEETH, NO_TEETH or NOT_RUN (REFUSED, #660) or
    UNCOMPARABLE (the base could not be judged: no evidence either way, never verified and never a
    refusal)."""

    kind: str
    code: str = ""
    detail: str = ""

    def worker_reason(self, *, isolated: bool, why: str = "") -> str:
        """Base runs come after WORKER_STARTED, and a worker with the owner's uid could have broken
        the base on purpose: only an isolated worker's attempt may say UNCOMPARABLE. ``why`` the
        worker is not isolated (AgyWorker.not_isolated_because) is kept in the reason (#234)."""
        head = "UNCOMPARABLE: " if isolated is True else ""
        tail = "" if isolated is True or not why else f" (worker not isolated: {why})"
        return f"{head}{self.code}: {_shown_detail(self.detail)}{tail}"


class BaseUncomparable(Exception):
    """Carries an UNCOMPARABLE Comparison. Not a TestCommitRejected, so no catch site reads it as
    REFUSED; each boundary maps it explicitly."""

    def __init__(self, comparison: Comparison) -> None:
        super().__init__(f"{comparison.code}: {_shown_detail(comparison.detail)}")
        self.comparison = comparison


def _base_problem(run) -> str:
    rc = run.returncode
    if run.forged:  # first, as p2p._probe: a scan after the run finds it, a timeout cannot cause it
        return "BASE_FORGED_BYTECODE"  # bytecode the run may not supply may have decided it (#481)
    if getattr(run, "data_refused", ()):  # #850: the refusal, not the base, may have failed it
        return "DATA_EXEC_REFUSED"
    if run.timed_out or rc is None:  # None without a timeout: no sandbox, nothing ran
        return "BASE_TIMEOUT" if run.timed_out else "BASE_NOT_EXECUTED"
    if run.survivors:  # a process outlived the base run (or cannot tell): as p2p._probe (#285)
        return "BASE_SURVIVORS"
    if run.mutated:  # the base's own tests wrote into the checkout, rc 0 too (as p2p._probe, #285)
        return "BASE_MUTATED"
    if run.report_too_big:
        return "BASE_REPORT_OVER_CAP"
    if run.log_too_big:  # the candidate run of the same tests would go over the cap too (#481)
        return "BASE_LOG_OVER_CAP"
    if run.collect_errors:  # the guard saw a collector fail (exit 2 alone may be an interrupt)
        return "BASE_COLLECTION_ERROR"
    if rc not in (0, 1):  # 5: nothing collected; 2 without collect_errors, 3, 4, others: pytest
        return "BASE_NO_TESTS" if rc == 5 else "BASE_PYTEST_ERROR"
    if not run.recorded:
        return "BASE_NO_REPORT"
    # #478: a teardown error (a shared fixture's lands on whichever test runs last), or exit 1
    # with no failed test in the record: the base run cannot say which tests fail on the base
    if run.teardown_errors or (rc == 1 and "failed" not in run.outcomes.values()):
        return "BASE_PYTEST_ERROR"
    return ""


def base_comparison(runs, targets: frozenset[str]) -> Comparison:
    """Every run is judged (no short-circuit): one uncomparable run makes it UNCOMPARABLE even when
    another has teeth. Then every new or changed test (``targets``) must be in some run's guard
    record (collected and run, any outcome), or the receipt would bind tests no verifier ran
    (NOT_RUN, #660; the static target rule, ``untargeted``, cannot see what pytest collects).
    Teeth: one of them failed in the guard's record."""
    hidden = any(getattr(run, "hidden_tests", False) for run in runs)
    problems = [(run, code) for run in runs if (code := _base_problem(run))]
    if problems:
        files = {file_part(target) for target in targets}
        detail = "; ".join(
            f"{run.spec.verifier_id}: {code} (exit {run.returncode})"
            + _collection_error_detail(run, hidden)
            + (survivor_argv.listing(run.survivors) if code == "BASE_SURVIVORS" else "")
            + no_tests_hint(run, code) + skip_hint(run)
            + (COLLECT_HINT if set(run.collect_errors) & files else "")
            for run, code in problems
        )  # fmt: skip
        return Comparison("UNCOMPARABLE", problems[0][1], detail)
    if missing := not_run(runs, targets):  # #660: the receipt would bind a test that never ran
        missing = str(len(missing)) if hidden else missing
        detail = f"no verifier collected or ran {missing}" + "".join(map(skip_hint, runs))
        return Comparison("NOT_RUN", "DEFERRED_TESTS_NOT_RUN", detail)
    verdict = base_teeth(runs, targets)  # #775: a fixture or OSError failure is not teeth
    if verdict[0] != "NO_TEETH":
        return Comparison(*verdict)
    codes = [run.returncode for run in runs]
    return Comparison("NO_TEETH", "TESTS_DO_NOT_FAIL_ON_BASE", f"pytest exit codes {codes}")


def _collection_error_detail(run, hidden):
    errors = run.collect_errors
    if not errors:
        return ""
    shown = str(len(errors)) if hidden else _names(errors)
    return f", collection errors {shown}"


def not_run(runs, targets) -> list[str]:
    """The ``targets`` no run's guard record holds (collected and run, any outcome; a
    parametrisation counts): the base run's check (#660) and the verification run's (#697)."""
    ran = {f for r in runs for n in r.outcomes for f in (_without_case(n), _without_case(n, True))}
    return sorted(set(targets) - ran)


def lay_tests_over(
    repo: Path, candidate: str, test_commit: str, paths: tuple[str, ...], attempt_id: str,
    run_root: Path, provenance: dict | None = None,
) -> str:  # fmt: skip
    """Commit the test files of test_commit on top of candidate; return the new commit."""
    index = Path(run_root) / f"{attempt_id}.tests.index"
    if index.exists() or index.is_symlink():
        raise TestCommitRejected("TEST_OVERLAY_FAILED", f"{index} already exists")
    try:
        tree = overlay_tree(repo, candidate, test_commit, paths, index, deleted_of(paths))
        message = f"Cairnwork attempt {attempt_id}: tests from {test_commit}{trailers(provenance)}"
        head = run_git(repo, *_IDENTITY, "commit-tree", tree, "-p", candidate, "-m", message)
        head = head.strip()
        run_git(repo, "update-ref", f"{ATTEMPT_REF_PREFIX}{attempt_id}-tested", head)
    except (GitCommandError, ValueError) as exc:
        raise TestCommitRejected("TEST_OVERLAY_FAILED", str(exc)) from exc
    finally:
        index.unlink(missing_ok=True)
    return head
