"""Receiver-side repeats of deferred-test path scope checks."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any

from cairnwork.deferred_tests import (
    _NOT_TEST_FILES,
    _SITE_NAMES,
    TestCommitRejected,
    test_paths,
)
from cairnwork_attest.verify_response import VerifyResult, _fail


def deleted_path_problem(paths: list[str]) -> str:
    """Check deleted names available in both receiver modes."""
    for path in paths:
        parts, name = PurePosixPath(path).parts, PurePosixPath(path).name.casefold()
        if (
            name in _NOT_TEST_FILES
            or name.endswith(".pth")
            or {part.casefold() for part in parts} & _SITE_NAMES
        ):
            return f"deleted test path {path[:200]!r} is out of scope"
    return ""


def full_paths_problem(
    repo: Path,
    base: str,
    test_commit: str,
    paths: list[str],
    deleted: list[str],
    roots: list[str] | None,
) -> str:
    """Re-derive the producer's test-commit scope from the commit available in full mode."""
    if not roots:
        # test_roots is producer configuration and is not recorded in the binding. Without a
        # policy pin, the receiver cannot independently know the roots used by test_paths().
        return ""
    try:
        actual, actual_deleted = test_paths(
            repo, base, test_commit, tuple(root.rstrip("/") for root in roots)
        )
    except TestCommitRejected as exc:
        return f"test commit scope: {exc}"
    # Producer bindings sort both lists (verify_candidate._binding); test_paths preserves Git's
    # diff order, so compare after applying that same serialization order.
    if sorted(actual) != paths or sorted(actual_deleted) != deleted:
        return "test commit paths fail the producer's scope rule"
    return ""


def roots_problem(
    policy: Any, binding: dict[str, Any], full: bool, repo: Path
) -> VerifyResult | None:
    """Check policy roots, deleted names, and full-mode producer scope when roots are pinned."""
    deleted = binding.get("deleted_test_paths", [])
    paths = binding["test_paths"]
    if policy is None or "test_roots" not in policy:
        if deleted and not full:
            return _fail("TEST_ROOT_VIOLATION", "deleted test paths need a policy with test_roots")
    else:
        roots = tuple(policy["test_roots"])
        for path in sorted([*paths, *deleted]):
            if not any(path.startswith(r.rstrip("/") + "/") for r in roots):
                return _fail(
                    "TEST_ROOT_VIOLATION", f"test path {path[:200]!r} not under any allowed root"
                )
    problem = deleted_path_problem(deleted)
    if problem:
        return _fail("TEST_SOURCE_MISMATCH", problem)
    if full:
        problem = full_paths_problem(
            repo,
            binding["base"],
            binding["test_commit"],
            paths,
            deleted,
            policy.get("test_roots") if policy is not None else None,
        )
        if problem:
            return _fail("TEST_SOURCE_MISMATCH", problem)
    return None
