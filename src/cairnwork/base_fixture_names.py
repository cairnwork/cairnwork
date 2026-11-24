"""#986 step 5b: the fixture names pytest itself collects on the base, for ``check_conftests``.

One owner collect-only run on the base commit with the recorder on (``record_fixtures``, see
fixture_probe.py). It targets the existing base test files the test commit changes, so it sees the
plugins, ``pytest_plugins`` and dynamic fixtures those files meet. The result is advisory
(``check_conftests`` only unions it with the static names, so it can add a refusal and never remove
one); any fault, an account run, a conftest-free test commit or no base target gives None, and the
#916 rule then stands alone, as before this step.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from cairnwork.base_tree_targets import base_tree_targets
from cairnwork.conftest_check import check_conftests, conftest_of, governed
from cairnwork.runtime_reach import check_runtime_reach
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


def check_with_base(ctx, task, paths: tuple[str, ...], run_id: str) -> None:
    """``check_conftests`` with the base's collected fixture names (None when not obtained), then
    the runtime plugin and sys.path reach (#1769; refusals only)."""
    run, reason = _probe(ctx, task, paths, run_id)
    check_conftests(
        task.repo, task.base_commit, task.test_commit, paths, collected=_names(run, reason)
    )
    if run is not None and run.collect_record is not None:
        on_base = tuple(p for p in paths if _on_base(task.repo, task.base_commit, p))
        check_runtime_reach(run.collect_record, on_base)


def collect(ctx, task, paths: tuple[str, ...], run_id: str):
    """The names the base run recorded, or None; a None is logged with its reason code."""
    return _names(*_probe(ctx, task, paths, run_id))


def _names(run, reason: str):
    names = None if run is None else run.fixture_names
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
    ACCOUNT_RUN, NO_CONFTEST_CHANGE (no conftest, and no changed file the base has),
    NO_BASE_TARGET (not attempted), RUN_FAULT, RUN_COUNT or NO_NAMES_RECORDED (attempted,
    nothing usable). With a conftest change the first try collects the base tests under its
    directory (#1767; a sibling directory is not governed by it). The base tree size alone (never a
    changed file) decides whether that is tried: when the tree is too big, unreadable or the run
    gives no names, the narrow run of the changed files follows, and its names come with
    TREE_OVER_CAP, TREE_UNREADABLE or TREE_RUN_FAILED instead of "OK". Diagnostics only."""
    run, reason = _probe(ctx, task, paths, run_id)
    return (None if run is None else run.fixture_names), reason


def _targets(task, paths: tuple[str, ...], conftests: tuple[str, ...]) -> tuple[str, ...]:
    """The changed .py files the base has. With none, but another changed base file (an archive,
    data), one base test file, even beside a conftest change: a mixed change still gets a run
    (#1769). A conftest-only commit still makes no run."""
    on_base = tuple(p for p in paths if _on_base(task.repo, task.base_commit, p))
    found = tuple(p for p in on_base if p.endswith(".py") and p not in conftests)
    if found or not any(p not in conftests for p in on_base):
        return found[:MAX_TARGETS]
    listing = subprocess.run(
        ["git", "-C", str(task.repo), "ls-tree", "-r", "--name-only", task.base_commit],
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    tests = [
        n for n in listing.splitlines() if n.endswith(".py") and Path(n).name.startswith("test")
    ]
    return tuple(tests[:1])


def _probe(ctx, task, paths: tuple[str, ...], run_id: str):
    """(run, reason): the base run and "OK" (or a TREE_ note), the run and NO_NAMES_RECORDED, or
    (None, a code)."""
    conftests = conftest_of(paths)
    if ctx.account is not None:
        return None, "ACCOUNT_RUN"
    try:
        targets = _targets(task, paths, conftests)
        if not conftests and not any(_on_base(task.repo, task.base_commit, p) for p in paths):
            return None, "NO_CONFTEST_CHANGE"
        if not targets:
            return None, "NO_BASE_TARGET"
        note = "OK"
        if conftests:  # the whole-tree run is for the tests a conftest governs (#1767)
            tree = base_tree_targets(task.repo, task.base_commit, tuple(task.test_roots))
            note = "TREE_UNREADABLE"
            if tree is not None:
                note = "TREE_OVER_CAP"
                if len(tree) <= MAX_TREE_TARGETS:  # the base alone decides: no changed file counts
                    under = set(governed(conftests, tree))
                    whole = (*targets, *(p for p in tree if p in under and p not in targets))
                    run, _ = _collect_run(ctx, task, whole, run_id)
                    if run is not None and run.fixture_names is not None:
                        return run, "OK"
                    note = "TREE_RUN_FAILED"
        run, reason = _collect_run(ctx, task, targets, f"{run_id}-n")
        if run is not None and run.fixture_names is not None:
            return run, note
        return run, reason
    except Exception:  # noqa: BLE001 - any fault means no names (the #916 rule)
        return None, "RUN_FAULT"  # never read the exception: the candidate shapes its name


def _collect_run(ctx, task, targets: tuple[str, ...], run_id: str):
    """One collect-only run with the recorder on: (run, "OK"), (run, NO_NAMES_RECORDED) or (None, a
    reason code)."""
    try:
        spec = VerifierSpec("fixture-names", targets, collect_only=True, record_fixtures=True)
        runs = run_verifiers(ctx, task.base_commit, (spec,), run_id)
        if len(runs) != 1:
            return None, "RUN_COUNT"
        run = runs[0]
        return run, ("OK" if run.fixture_names is not None else "NO_NAMES_RECORDED")
    except Exception:  # noqa: BLE001 - any fault means no names (the #916 rule)
        return None, "RUN_FAULT"  # never read the exception: the candidate shapes its name
