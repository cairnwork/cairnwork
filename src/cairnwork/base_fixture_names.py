"""#986 step 5b: the fixture names pytest itself collects on the base, for ``check_conftests``.

Two owner collect-only runs on the base commit with the recorder on (``record_fixtures``, see
fixture_probe.py).
1. The names run (``probe``): the base tests under a conftest the test commit adds or edits, plus
   the changed base files. It sees the plugins, ``pytest_plugins`` and dynamic fixtures those files
   meet. The result is advisory (``check_conftests`` only unions it with the static names, so it
   can add a refusal and never remove one); any fault, an account run or a conftest-free commit
   gives None, and the #916 rule then stands alone.
2. The reach run (``reach_record``, #1769): whenever the commit changes a base file or adds a file
   (a conftest-only edit makes none), the base's test tree (``base_tree_targets``: every file up to
   MAX_TREE_TARGETS, else one file) plus one target below each conftest directory, so every
   conftest loads, plus the changed base .py files. ``check_runtime_reach`` refuses on the runtime
   record. A changed file that cannot be collected is dropped and the run repeated; a record that
   still leaves something out (tree unreadable, no plan, a fault) refuses too.
"""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path, PurePosixPath

from cairnwork.base_tree_targets import base_tree_targets, conftest_dirs, pick_dir_targets
from cairnwork.conftest_check import check_conftests, conftest_of, governed
from cairnwork.deferred_overlay import TestCommitRejected
from cairnwork.gitcmd import run_git_bytes
from cairnwork.runtime_reach import CODE, check_runtime_reach
from cairnwork.verifier_spec import VerifierSpec
from cairnwork.verifiers import run_verifiers

MAX_TARGETS = 20  # the narrow run: existing base files the test commit changes
MAX_TREE_TARGETS = 300  # the whole-tree run (#1767); a larger base keeps the narrow run
LOG = logging.getLogger(__name__)
LOUD = ("RUN_FAULT", "RUN_COUNT", "NO_NAMES_RECORDED")  # attempted, gave nothing
TREE_NOTES = ("TREE_OVER_CAP", "TREE_UNREADABLE", "TREE_RUN_FAILED")  # names: narrow run only


def _on_base(repo: Path, base: str, path: str) -> bool:
    done = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "-e", f"{base}:{path}"],
        capture_output=True,
        check=False,
    )
    return done.returncode == 0


def check_with_base(ctx, task, paths: tuple[str, ...], run_id: str, deleted=()) -> None:
    """``check_conftests`` with the base's collected fixture names (None when not obtained), then
    the runtime plugin and sys.path reach (#1769; refusals only)."""
    collected = collect(ctx, task, paths, run_id)
    check_conftests(task.repo, task.base_commit, task.test_commit, paths, collected=collected)
    on_base = (*(p for p in paths if _on_base(task.repo, task.base_commit, p)), *deleted)
    added = tuple(p for p in paths if p not in on_base)
    if not added and all(PurePosixPath(p).name == "conftest.py" for p in on_base):
        return  # only conftest edits: no run, as before (#1769)
    record, complete = reach_record(ctx, task, on_base, run_id)
    if record is None:
        return  # no run, no evidence: the static verdict stands
    check_runtime_reach(record, on_base, added, _base_paths(task))
    if not complete:  # fail closed: a record that left out a changed file or part of the tree
        raise TestCommitRejected(CODE, "the base run did not cover every changed or base test file")


def _base_paths(task) -> tuple[str, ...] | None:
    """Every file of the base (None when unreadable: a case variant is then the same file)."""
    try:
        raw = run_git_bytes(task.repo, "ls-tree", "-r", "-z", "--name-only", task.base_commit, "--")
        return tuple(p for p in raw.decode("utf-8", "surrogateescape").split("\0") if p)
    except Exception:  # noqa: BLE001 - unknown base: the folded match stands
        return None


def reach_record(ctx, task, changed: tuple[str, ...], run_id: str):
    """(record, complete): the record of one collect-only base run, or (None, False).

    The run names the changed base .py files, every base conftest directory (a test file below it,
    else its conftest.py: collecting either loads it), and the whole test tree up to
    MAX_TREE_TARGETS files (above that one tree file, so a root conftest and the ini still load).
    An unreadable tree, unreadable conftest directories or more than MAX_CONFTEST_DIRS of them
    refuse the commit (TestCommitRejected): no run is made. ``complete`` is False when a changed
    file that cannot be collected on the base was dropped (the rest run again, the directories
    picked again from the tree minus that file) and its base text shows a sys.path or plugin
    signal or cannot be read."""
    if ctx.account is not None:
        return None, False
    roots = tuple(task.test_roots)
    tree = base_tree_targets(task.repo, task.base_commit, roots)
    dirs = conftest_dirs(task.repo, task.base_commit, roots)
    if tree is None or dirs is None or pick_dir_targets(tree, dirs) is None:
        raise TestCommitRejected(
            CODE, "the base's test tree or conftest directories were not usable"
        )
    try:
        edited = tuple(
            p for p in changed if p.endswith(".py") and PurePosixPath(p).name != "conftest.py"
        )
        for dropped, tag in ((frozenset(), "r"), (frozenset(edited), "r2")):
            if tag == "r2" and not edited:
                break
            left = tuple(p for p in tree if p not in dropped)
            picked = pick_dir_targets(left, dirs) or ()
            base = left if len(tree) <= MAX_TREE_TARGETS else left[:1]
            named = (*(p for p in edited[:MAX_TARGETS] if p not in dropped), *base, *picked)
            targets = tuple(dict.fromkeys(named)) or roots  # roots: the root conftest and ini load
            record = _record_run(ctx, task, targets, f"{run_id}-{tag}")
            if record is not None:
                return record, not dropped or _quiet(task, edited)
    except Exception:  # noqa: BLE001 - any fault means no record: the static verdict stands
        return None, False
    return None, False


SIGNAL = re.compile(rb"sys|path|plugin|importlib|import_module|__import__|exec|eval|site")


def _quiet(task, dropped: tuple[str, ...]) -> bool:
    """True when no dropped file's base text shows a sys.path or plugin signal (unreadable: no)."""
    try:
        return not any(
            SIGNAL.search(run_git_bytes(task.repo, "show", f"{task.base_commit}:{p}"))
            for p in dropped
        )
    except Exception:  # noqa: BLE001 - unreadable is never quiet
        return False


def _record_run(ctx, task, targets: tuple[str, ...], run_id: str):
    try:
        spec = VerifierSpec("fixture-names", targets, collect_only=True, record_fixtures=True)
        runs = run_verifiers(ctx, task.base_commit, (spec,), run_id)
        return runs[0].collect_record if len(runs) == 1 else None
    except Exception:  # noqa: BLE001 - never read the exception: the candidate shapes its name
        return None


def collect(ctx, task, paths: tuple[str, ...], run_id: str):
    """The names the base run recorded, or None; a None is logged with its reason code."""
    names, reason = probe(ctx, task, paths, run_id)
    if names is not None and reason in TREE_NOTES:
        _log(logging.WARNING, "base fixture names from the narrow run only (%s)", reason)
    if names is None:
        level = logging.WARNING if reason in LOUD else logging.INFO
        _log(level, "base fixture names not used (%s): the #916 rule stands alone", reason)
    return names


def _log(level: int, message: str, reason: str) -> None:
    try:
        LOG.log(level, message, reason)
    except Exception:  # noqa: BLE001, S110 - a failing log handler must not change the verdict
        pass


def probe(ctx, task, paths: tuple[str, ...], run_id: str):
    """(names, reason): the names the base run recorded and "OK", or (None, a reason code):
    ACCOUNT_RUN, NO_CONFTEST_CHANGE, NO_BASE_TARGET (not attempted), RUN_FAULT,
    RUN_COUNT or NO_NAMES_RECORDED (attempted, nothing usable). The first try collects the base
    tests under a conftest's directory the test commit adds or edits (#1767; a sibling directory is
    not governed by it). The base tree size alone (never a changed file) decides whether that is
    tried: when the tree is too big, unreadable or the run gives no names, the narrow run of the
    changed files follows, and its names come with TREE_OVER_CAP, TREE_UNREADABLE or
    TREE_RUN_FAILED instead of "OK". Diagnostics only."""
    conftests = conftest_of(paths)
    if ctx.account is not None:
        return None, "ACCOUNT_RUN"
    if not conftests:
        return None, "NO_CONFTEST_CHANGE"
    try:
        narrow = tuple(
            p
            for p in paths
            if p.endswith(".py") and p not in conftests and _on_base(task.repo, task.base_commit, p)
        )[:MAX_TARGETS]
        if not narrow:
            return None, "NO_BASE_TARGET"
        tree = base_tree_targets(task.repo, task.base_commit, tuple(task.test_roots))
        note = "TREE_UNREADABLE"
        if tree is not None:
            note = "TREE_OVER_CAP"
            if len(tree) <= MAX_TREE_TARGETS:  # the base alone decides: no changed file counts
                under = set(governed(conftests, tree))
                whole = (*narrow, *(p for p in tree if p in under and p not in narrow))
                names, _ = _collect_run(ctx, task, whole, run_id)
                if names is not None:
                    return names, "OK"
                note = "TREE_RUN_FAILED"
        names, reason = _collect_run(ctx, task, narrow, f"{run_id}-n")
        return (names, note) if names is not None else (None, reason)
    except Exception:  # noqa: BLE001 - any fault means no names (the #916 rule)
        return None, "RUN_FAULT"  # never read the exception: the candidate shapes its name


def _collect_run(ctx, task, targets: tuple[str, ...], run_id: str):
    """One collect-only run with the recorder on: (names, "OK") or (None, a reason code)."""
    try:
        spec = VerifierSpec("fixture-names", targets, collect_only=True, record_fixtures=True)
        runs = run_verifiers(ctx, task.base_commit, (spec,), run_id)
        if len(runs) != 1:
            return None, "RUN_COUNT"
        names = runs[0].fixture_names
        return (names, "OK") if names is not None else (None, "NO_NAMES_RECORDED")
    except Exception:  # noqa: BLE001 - any fault means no names; never read the exception
        return None, "RUN_FAULT"
