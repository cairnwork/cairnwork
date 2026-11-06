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
import fnmatch
import hashlib
import re
import shlex
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from cairnwork.collect_reader import read_collected
from cairnwork.conftest_check import conftest_of
from cairnwork.deferred_tests import BaseUncomparable, Comparison, TestCommitRejected
from cairnwork.gitcmd import GitCommandError, run_git
from cairnwork.p2p_nodes import changed_tests, module_changed
from cairnwork.p2p_selection import MODULE_DIR_CAP
from cairnwork.pytest_config import PytestConfigError, _render, base_settings
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
    # #798: base tests of a file whose module-level code the test commit changes (with a conftest
    # in the commit): left out of the differential probe's comparison only; P2P still runs them
    probe_only: frozenset[str] = frozenset()

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
    probe_only: frozenset[str] = frozenset()
    if conftest_of(test_files):  # #798: module-level code of an edited file may use a new fixture
        over = module_changed(task.repo, task.base_commit, task.test_commit, paths)
        probe_only = frozenset(_function(n) for n in base_ids & test_ids if _file(n) in over)
    return CollectedSide(
        side.base_ids, side.test_ids, side.removed, side.dropped, edited, changed, probe_only
    )


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


OVER_BUDGET = ("base-probe-timeout", "base-probe-report-over-cap", "base-probe-log-over-cap")


def no_targets(rule: dict, existing, why: str = "") -> BaseUncomparable:
    """#793 Step 3: the base has test files under the roots but none can be a regression target
    (the selection holds none, the whole base is over the cap or the budget): UNCOMPARABLE
    ``P2P_NO_TARGETS``, never verified."""
    detail = f"{len(existing)} base test file(s), none usable in {rule['applied']} mode"
    detail += f" ({why})" if why else ""
    cut = rule.get("capped_dirs")  # #333: a cut is named
    detail += f"; capped: {', '.join(cut)} (over {rule['cap']} test files)" if cut else ""
    return BaseUncomparable(Comparison("UNCOMPARABLE", "P2P_NO_TARGETS", detail))


def whole_base(rule: dict, existing) -> tuple[str, ...]:
    """#793 Step 3 (c): module-dir selected nothing, so the regression comparison is every base
    test file under the roots, when that is within the cap; a cut or a larger base is
    UNCOMPARABLE (the budget is checked by the probe that runs them)."""
    if rule.get("capped_dirs") or len(existing) > MODULE_DIR_CAP:
        raise no_targets(rule, existing, f"over {MODULE_DIR_CAP} test files")
    rule |= {"selected_files": len(existing), "fallback": "all-base-tests"}
    return existing


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


def untargetable(task, paths, target) -> list[str]:
    """#930: base test files no verifier target can name (a path part outside the target grammar,
    such as a dot or a non-ASCII letter) that pytest still collects: none of their directories
    below the test root is one the base's ``norecursedirs`` skips (skipped_dir)."""
    roots, skipped = task.test_roots, skipped_dir(task.repo, task.base_commit)

    def below(path: str) -> list[str]:
        root = max((r for r in roots if path.startswith(f"{r}/")), key=len, default="")
        return path[len(root) :].strip("/").split("/")[:-1]

    return [p for p in paths if not target.fullmatch(p) and not any(map(skipped, below(p)))]


# pytest's default norecursedirs (9.x), used when the base sets none
DEFAULT_NORECURSEDIRS = ("*.egg", ".*", "_darcs", "build", "CVS", "dist", "node_modules", "venv",
                         "{arch}")  # fmt: skip


def skipped_dir(repo, base_commit) -> Callable[[str], bool]:
    """Whether pytest's recursion skips a directory of this name, by the base's ``norecursedirs``
    (its own patterns replace the default, as in pytest). A pattern with ``/`` matches a whole
    path, never a bare name, so it skips nothing here: such files are refused, never dropped."""
    value = dict(base_settings(repo, base_commit).values).get("norecursedirs")
    if value is None:
        patterns: tuple[str, ...] = DEFAULT_NORECURSEDIRS
    elif isinstance(value, list):
        patterns = tuple(str(item) for item in value)
    else:
        try:
            patterns = tuple(shlex.split(_render(value) or ""))
        except ValueError as exc:
            raise PytestConfigError("norecursedirs at the base cannot be split") from exc
    return lambda name: any(fnmatch.fnmatch(name, pattern) for pattern in patterns)
