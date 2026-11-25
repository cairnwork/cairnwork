"""Two helpers of a verify run: the receipt binding with deferred tests (#229), and the removal
of the run's scratch clone."""

from __future__ import annotations

from typing import Any

from cairnwork.deferred_tests import OVERLAY_RULE
from cairnwork.gitcmd import run_git
from cairnwork.notary_verify import binding_extras
from cairnwork.workspace import WorkspaceError, remove_attempt_clone


def binding_of(task, head: str, test_files: tuple[str, ...], judged: str, plan) -> dict[str, Any]:
    """#229: what the receipt is bound to. The scratch commit that carried the tests dies with
    the scratch clone and its id cannot be rebuilt (commit time), so the receipt names the PR head,
    the tests and the tree that ran, which a receiver rebuilds with deferred_tests.overlay_tree."""
    tree = run_git(task.repo, "rev-parse", f"{judged}^{{tree}}").strip()
    return binding_extras(task, plan, test_files) | {
        "rule": OVERLAY_RULE, "base": task.base_commit, "head": head,
        "test_commit": task.test_commit, "test_paths": sorted(test_files), "executed_tree": tree,
        "verifiers": [[verifier_id, list(argv)] for verifier_id, argv in task.verifiers],
    }  # fmt: skip


def remove_clone(run_root, result) -> None:
    try:
        remove_attempt_clone(run_root, result.attempt_id)
    except WorkspaceError as exc:  # keep the run's own outcome; note the leftover
        result.logs.append(f"cleanup failed: {exc}")
