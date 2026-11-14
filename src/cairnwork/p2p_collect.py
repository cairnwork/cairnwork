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
AST classification has a 10-second process limit; timeout or child failure is refused
(``TEST_COMMIT_ANALYSIS_LIMIT``).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

from cairnwork import p2p_classify_child as _classify_child
from cairnwork.collect_reader import read_collected
from cairnwork.conftest_check import conftest_of
from cairnwork.deferred_tests import BaseUncomparable, Comparison, TestCommitRejected
from cairnwork.gitcmd import GitCommandError, run_git
from cairnwork.node_ids import show, unmatched_note
from cairnwork.p2p_nodes import changed_tests, module_changed
from cairnwork.p2p_selection import MODULE_DIR_CAP
from cairnwork.verifier_spec import VerifierSpec
from cairnwork.verifiers import run_verifiers

_base_may_supply = _classify_child._base_may_supply
_class_binds_hook = _classify_child._class_binds_hook
_defines = _classify_child._defines
_hidden = _classify_child._hidden
_is_unambiguous_testcase = _classify_child._is_unambiguous_testcase
_mentions = _classify_child._mentions
_mentions_dunder_test = _classify_child._mentions_dunder_test
_module_mutates_base = _classify_child._module_mutates_base
_scope_mentions = _classify_child._scope_mentions
_tree = _classify_child._tree

CLASSIFY_TIMEOUT_SECONDS = 10
CLASSIFY_OUTPUT_CAP = 1024 * 1024


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
        detail = _named(sorted(side.dropped))
        note = unmatched_note(side.dropped, side.test_ids)
        raise TestCommitRejected(
            "TEST_COMMIT_DROPS_BASE_TESTS", f"{detail}; {note}" if note else detail
        )
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
    """Classify missing ids in a bounded subprocess; source callables are read in the parent."""
    base_ids, test_ids, edited = frozenset(base_ids), frozenset(test_ids), frozenset(edited)
    collected_funcs = {_function(nodeid) for nodeid in test_ids}
    needed = {
        _file(nodeid)
        for nodeid in base_ids - test_ids
        if _file(nodeid) in edited and _function(nodeid) not in collected_funcs
    }
    sources = {
        "test": {path: source(path) for path in sorted(needed)},
        "base": {path: base_source(path) for path in sorted(needed)},
    }
    removed, dropped = _run_classification(base_ids, test_ids, edited, sources)
    return CollectedSide(base_ids, test_ids, removed, dropped)


def _run_classification(base_ids, test_ids, edited, sources):
    payload = json.dumps(
        {
            "base_ids": sorted(base_ids),
            "test_ids": sorted(test_ids),
            "edited": sorted(edited),
            "sources": sources,
        }
    ).encode()
    output, timed_out, returncode = _run_child(_classify_argv(), payload)
    if timed_out:
        _analysis_failure("time limit exceeded")
    if returncode != 0:
        _analysis_failure("child process failed")
    removed_set: set[str] = set()
    dropped_set: set[str] = set()
    try:
        result = json.loads(output)
        raw_removed, raw_dropped = result["removed"], result["dropped"]
        if set(result) != {"removed", "dropped"}:
            raise ValueError
        if not isinstance(raw_removed, list) or not isinstance(raw_dropped, list):
            raise ValueError
        if any(not isinstance(item, str) for item in raw_removed + raw_dropped):
            raise ValueError
        if len(raw_removed) != len(set(raw_removed)) or len(raw_dropped) != len(set(raw_dropped)):
            raise ValueError
        base = set(base_ids)
        expected = base - set(test_ids)
        removed_set, dropped_set = set(raw_removed), set(raw_dropped)
        if not removed_set <= base or not dropped_set <= base or removed_set & dropped_set:
            raise ValueError
        if removed_set | dropped_set != expected:
            raise ValueError
    except (json.JSONDecodeError, KeyError, TypeError, ValueError, RecursionError):
        _analysis_failure("invalid child result")
    return frozenset(removed_set), frozenset(dropped_set)


def _classify_argv():
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]); "
        "from cairnwork.p2p_classify_child import main; main()"
    )
    return [
        sys.executable,
        "-I",
        "-X",
        "utf8",
        "-c",
        code,
        str(Path(__file__).resolve().parent.parent),
    ]


def _run_child(argv, payload):
    output: list[bytes] = []
    size = 0
    process = None
    try:
        process = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env={},
            start_new_session=True,
        )
        stdout = process.stdout
        assert stdout is not None

        def drain():
            nonlocal size
            while chunk := stdout.read(8192):
                if size < CLASSIFY_OUTPUT_CAP:
                    kept = chunk[: CLASSIFY_OUTPUT_CAP - size]
                    output.append(kept)
                    size += len(kept)

        reader = threading.Thread(target=drain, daemon=True)
        writer = threading.Thread(target=_write_input, args=(process.stdin, payload), daemon=True)
        reader.start()
        writer.start()
        try:
            process.wait(timeout=CLASSIFY_TIMEOUT_SECONDS)
            timed_out = False
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_child_group(process.pid)
            process.wait()
        _kill_child_group(process.pid)
        writer.join(timeout=1)
        reader.join(timeout=1)
        if reader.is_alive() or writer.is_alive() or size >= CLASSIFY_OUTPUT_CAP:
            return b"", timed_out, 1
        return b"".join(output), timed_out, process.returncode
    except (OSError, RuntimeError):  # RuntimeError: a thread that cannot start
        return b"", False, 1
    finally:
        if process is not None:  # every exit path: kill the whole group, reap the child
            _kill_child_group(process.pid)
            process.wait()


def _write_input(stream, payload):
    try:
        stream.write(payload)
        stream.close()
    except (BrokenPipeError, OSError):
        pass


def _kill_child_group(group):
    try:
        os.killpg(group, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _analysis_failure(detail):
    raise TestCommitRejected("TEST_COMMIT_ANALYSIS_LIMIT", detail)


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
    shown = ", ".join(map(show, ids[:5]))
    return f"{len(ids)} base test(s): {shown}" + (" ..." if len(ids) > 5 else "")


def under_roots(raw: bytes, roots) -> list[str]:
    """Tracked paths (``ls-tree -z`` output) under the test roots, compared case-folded (#1001: on
    APFS a tracked Tests/ is the tests/ root pytest runs, but a git pathspec spelled tests misses
    it); a name that is not UTF-8 is kept, never a crash."""
    folded = tuple(f"{root.casefold()}/" for root in roots)
    names = raw.decode("utf-8", "surrogateescape").split("\0")
    return [p for p in names if p.casefold().startswith(folded)]
