"""#793 Step 2: which base tests the test commit removed, from what pytest collected.

The P2P base probe (p2p.py) records the node ids pytest ran on the base for the selected files
(``base_ids``). The same files are then collected, ``--collect-only`` in the same sandbox, ini and
guard (collect_reader.py), on the test commit (``test_ids``). A base id missing there is:

* ``removed`` when the test commit edited its file (a deferred test file), the base file defines
  the test there (a ``def`` of that name in the node id's class, or at module level), and the
  name no longer appears in that file's source on the test commit: the author removed it, so
  its base outcome says nothing about the candidate and it is not required;
* ``dropped`` otherwise. The test commit made pytest stop collecting a base test without
  removing it (a changed base class, decorator, condition, or a test the base file inherits or
  builds dynamically, whose name was never in that file): the test commit is refused
  (``TEST_COMMIT_DROPS_BASE_TESTS``), never read as "nothing to run".

A base id in a file whose collection failed or was skipped on the test commit is unknown, never
removed: refused (``TEST_COMMIT_COLLECT_ERROR``). A collect-only run that cannot be read is
UNCOMPARABLE (``P2P_TEST_COLLECT_UNUSABLE``). ``changed`` is the AST body comparison of tests
present on both commits (p2p_nodes.changed_tests), limited to tests pytest collected on both
sides; a test the AST reader cannot see stays a P2P test (at worst a wrong refusal, never a
wrong acceptance). The removed tests join ``changed``: both are left to the author's verifiers.
"""

from __future__ import annotations

import ast
import hashlib
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from cairnwork.collect_reader import read_collected
from cairnwork.deferred_tests import BaseUncomparable, Comparison, TestCommitRejected
from cairnwork.gitcmd import GitCommandError, run_git
from cairnwork.p2p_nodes import changed_tests
from cairnwork.verifier_spec import VerifierSpec
from cairnwork.verifiers import run_verifiers


@dataclass(frozen=True)
class CollectedSide:
    base_ids: frozenset[str]  # node ids the base probe ran in the selected files
    test_ids: frozenset[str]  # node ids pytest collected in those files on the test commit
    removed: frozenset[str]  # base ids the test commit removed (not required on the candidate)
    dropped: frozenset[str]  # base ids no longer collected but not removed: refused
    changed_files: frozenset[str] = frozenset()  # selected files the test commit edits
    changed: frozenset[str] = frozenset()  # path::function ids left to the author's verifiers

    def note(self) -> dict:
        """Bound into the P2P rule: counts and hashes, so the claim is auditable."""
        base, test = self.base_ids, self.test_ids
        return {"base_ran": len(base), "base_ran_sha256": _digest(base),
                "test_collected": len(test), "test_collected_sha256": _digest(test),
                "removed": len(self.removed)}  # fmt: skip


def collected_side(task, files, outcomes, test_files, ctx, budget, attempt_id) -> CollectedSide:
    """The test-commit side of the selected ``files``; see the module docstring."""
    base_ids = frozenset(outcomes)
    present = _present(task, files)
    test_ids: frozenset[str] = frozenset()
    if present:
        got = _collect(task, present, ctx, budget, attempt_id)
        unknown = sorted(n for n in base_ids if _file(n) in {*got.error_files, *got.skipped_files})
        if unknown:
            raise TestCommitRejected("TEST_COMMIT_COLLECT_ERROR", _named(unknown))
        test_ids = frozenset(got.ids)
    edited = frozenset(test_files) & frozenset(files)
    there = frozenset(present)
    side = classify(
        base_ids,
        test_ids,
        edited,
        lambda p: _source(task, task.test_commit, p) if p in there else "",
        lambda p: _source(task, task.base_commit, p),
    )
    if side.dropped:
        raise TestCommitRejected("TEST_COMMIT_DROPS_BASE_TESTS", _named(sorted(side.dropped)))
    paths = tuple(sorted(edited))
    both = {_function(n) for n in base_ids & test_ids}
    body = changed_tests(task.repo, task.base_commit, task.test_commit, paths) if paths else set()
    changed = frozenset(body & both) | {_function(n) for n in side.removed}
    return CollectedSide(side.base_ids, side.test_ids, side.removed, side.dropped, edited, changed)


def classify(base_ids, test_ids, edited, source, base_source) -> CollectedSide:
    """Split the base ids the test commit no longer collects into removed and dropped.

    ``source`` / ``base_source`` give a file's text on the test commit / the base."""
    removed, dropped, texts, bases = set(), set(), {}, {}
    for nodeid in base_ids - test_ids:
        path = _file(nodeid)
        gone = False
        if path in edited:
            if path not in texts:
                texts[path], bases[path] = source(path), _tree(base_source(path))
            *owners, name = _function(nodeid).split("::")[1:]
            gone = _defines(bases[path], owners, name) and not re.search(
                rf"\b{re.escape(name)}\b", texts[path]
            )
        (removed if gone else dropped).add(nodeid)
    return CollectedSide(
        frozenset(base_ids), frozenset(test_ids), frozenset(removed), frozenset(dropped)
    )


def _collect(task, files, ctx, budget, attempt_id):
    spec = VerifierSpec("p2p-collect", files, collect_only=True)
    runs = run_verifiers(ctx, task.test_commit, (spec,), f"{attempt_id}-c", timeout=budget)
    try:
        got = read_collected(runs[0])
    finally:
        for run in runs:  # evidence for this plan only, not for Core (as the base probe)
            shutil.rmtree(Path(run.log_path).parent, ignore_errors=True)
    if got.unusable:
        detail = f"{got.unusable} on the test commit"
        raise BaseUncomparable(Comparison("UNCOMPARABLE", "P2P_TEST_COLLECT_UNUSABLE", detail))
    return got


def _present(task, files) -> tuple[str, ...]:
    """The selected files that exist on the test commit (a deleted one is collected as absent)."""
    try:
        raw = run_git(
            task.repo, "ls-tree", "-r", "-z", "--name-only", task.test_commit, "--", *files
        )
    except GitCommandError as exc:
        raise TestCommitRejected("TEST_COMMIT_UNREADABLE", str(exc)) from exc
    there = set(raw.split("\0"))
    return tuple(f for f in files if f in there)


def _tree(text: str) -> ast.Module | None:
    try:
        return ast.parse(text)
    except (SyntaxError, ValueError):
        return None  # nothing is provably defined: the id is dropped, never removed


def _defines(tree: ast.Module | None, owners: list[str], name: str) -> bool:
    """Whether the base file itself has ``def name`` in the class chain ``owners``."""
    body = tree.body if tree is not None else []
    for owner in owners:
        found = [n for n in body if isinstance(n, ast.ClassDef) and n.name == owner]
        if not found:
            return False
        body = found[-1].body
    kinds = (ast.FunctionDef, ast.AsyncFunctionDef)
    return any(isinstance(n, kinds) and n.name == name for n in body)


def _source(task, commit: str, path: str) -> str:
    """The file's text on ``commit`` (it is there: a failed read is a refusal)."""
    try:
        return run_git(task.repo, "show", f"{commit}:{path}")
    except GitCommandError as exc:
        raise TestCommitRejected("TEST_COMMIT_UNREADABLE", str(exc)) from exc
    except UnicodeDecodeError as exc:
        raise TestCommitRejected("TEST_COMMIT_UNPARSABLE", f"{path} is not UTF-8") from exc


def _file(nodeid: str) -> str:
    return nodeid.partition("::")[0]


def _function(nodeid: str) -> str:
    return nodeid.split("[", 1)[0]


def _digest(ids) -> str:
    return hashlib.sha256("\n".join(sorted(ids)).encode()).hexdigest()


def _named(ids: list[str]) -> str:
    return f"{len(ids)} base test(s): " + ", ".join(ids[:5]) + (" ..." if len(ids) > 5 else "")
