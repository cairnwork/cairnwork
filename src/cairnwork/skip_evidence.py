"""Recognise environment-only verifier skips without treating candidate skips as evidence."""

from __future__ import annotations

import re
import shutil
from pathlib import Path, PurePosixPath
from typing import Any

from cairnwork.deferred_tests import BaseUncomparable, Comparison
from cairnwork.gitcmd import GitCommandError, run_git
from cairnwork.textclean import replace_hidden
from cairnwork.verifiers import run_verifiers

ENV_SKIP_REASONS = (
    "needs macOS sandbox-exec",
    "needs the cairnwork-verifier account",
)
_DELIMITERS = re.compile(r"[\[\];:]")


def env_skip_comparison(
    runs: tuple[Any, ...] | list[Any],
    repo: Path,
    base: str,
    judged: str,
    base_skips: dict[str, dict[str, tuple[str, str]]] | None = None,
    deferred_files: tuple[str, ...] = (),
    *,
    base_nodes: dict[str, tuple[str, ...]] | None = None,
    checks: tuple[Any, ...] = (),
) -> Comparison | None:
    """Return UNCOMPARABLE only when every verifier has trusted all-skipped evidence."""
    changed = _changed_paths(repo, base, judged)
    if changed is None or not runs or any(not check.observation.passed for check in checks):
        return None
    details = []
    for run in runs:
        detail = _run_detail(
            run, changed, repo, base, base_skips or {}, deferred_files, base_nodes or {}
        )
        if detail is None:
            return None
        details.append(detail)
    return Comparison("UNCOMPARABLE", "TESTS_SKIPPED_ENVIRONMENT", "; ".join(details))


def raise_for_env_skips(
    runs, task, judged, test_files=None, checks=(), enabled=False, base=None
) -> None:
    """Raise the verify-boundary signal when every verifier lacks environment evidence.
    ``base`` is ``(ctx, specs)``: with no deferred-test base run to compare against, the
    verifiers are run once on the base commit, only when every candidate run is all environment
    skips (#1151), so the comparison is never skipped for want of a record."""
    if not enabled:
        return
    base_skips = getattr(test_files, "base_skips", {})
    base_nodes = getattr(test_files, "base_nodes", {})
    if not base_skips and base and runs and all(_env_skips(run) for run in runs):
        base_skips, base_nodes = _base_run_record(task, *base)
    comparison = env_skip_comparison(
        runs,
        task.repo,
        task.base_commit,
        judged,
        base_skips,
        tuple(test_files or ()),
        base_nodes=base_nodes,
        checks=checks,
    )
    if comparison:
        raise BaseUncomparable(comparison)


def _base_run_record(task, ctx, specs):
    """Per verifier, the skips and node ids of one run of ``specs`` on the base commit; empty
    (so the candidate stays FAILED) when that run cannot be made."""
    try:
        runs = run_verifiers(ctx, task.base_commit, specs, f"{ctx.attempt_id}-eb", deny=True)
    except Exception:  # noqa: BLE001  (any base-run failure: no evidence, fail closed)
        return {}, {}
    try:
        recorded = [run for run in runs if not run.spec.collect_only and _env_skips(run)]
        skips = {run.spec.verifier_id: run.skips for run in recorded}
        nodes = {run.spec.verifier_id: tuple(run.outcomes) for run in recorded}
    finally:  # a base run's logs are not evidence for Core and could show tests to a later worker
        for run in runs:
            shutil.rmtree(Path(run.log_path).parent, ignore_errors=True)
    return skips, nodes


def _changed_paths(repo: Path, base: str, judged: str) -> set[str] | None:
    try:
        return set(run_git(repo, "diff", "--name-only", "-z", base, judged).split("\0"))
    except GitCommandError:
        return None


def _run_detail(
    run: Any,
    changed: set[str],
    repo: Path,
    base: str,
    base_skips: dict[str, dict[str, tuple[str, str]]],
    deferred_files: tuple[str, ...],
    base_nodes: dict[str, tuple[str, ...]],
) -> str | None:
    evidence = _env_skips(run)
    if evidence is None:
        return None
    collected: tuple[str, ...] = tuple(run.collected)
    skips: dict[str, tuple[str, str]] = run.skips
    files_collected = {_node_file(node) for node in collected}
    if deferred_files and not set(deferred_files) <= files_collected:
        return None
    if not base_skips or base_skips.get(run.spec.verifier_id) != skips:
        return None
    if set(base_nodes.get(run.spec.verifier_id, ())) != set(collected):
        return None
    files = files_collected
    if "" in files or any(not _base_file(repo, base, path, changed) for path in files):
        return None
    reasons = ", ".join(dict.fromkeys(_clean(reason) for _, reason in evidence))
    listed = ", ".join(sorted(files))
    return f"{_verifier_name(run.spec.verifier_id)}: {len(collected)} skipped ({reasons}; {listed})"


def _verifier_name(value: str) -> str:
    return value if re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", value) else "verifier"


def _clean_run(run: Any) -> bool:
    return (
        run.returncode == 0
        and run.recorded
        and not run.timed_out
        and not run.mutated
        and not run.survivors
        and not run.log_too_big
        and not run.report_too_big
    )


def _node_file(node: str) -> str:
    path = node.partition("::")[0]
    if "\\" in path or PurePosixPath(path).is_absolute() or ".." in path.split("/"):
        return ""
    return path if path.startswith("tests/") else ""


def _base_file(repo: Path, base: str, path: str, changed: set[str]) -> bool:
    if path in changed:
        return False
    try:
        run_git(repo, "cat-file", "-e", f"{base}:{path}")
    except GitCommandError:
        return False
    return True


def _clean(reason: str) -> str:
    return _DELIMITERS.sub("?", replace_hidden(reason))[:120]


def _env_skips(run: Any) -> list[tuple[str, str]] | None:
    """The ``(kind, reason)`` of each collected test of a clean run in which every test was
    skipped by a whitelisted environment marker; None for any other run."""
    collected: tuple[str, ...] = tuple(getattr(run, "collected", ()))
    outcomes = getattr(run, "outcomes", {})
    skips: dict[str, tuple[str, str]] = getattr(run, "skips", {})
    if (
        not _clean_run(run)
        or not getattr(run, "collection_finished", False)
        or getattr(run, "collect_errors", ())
        or getattr(run, "collect_skipped", ())
        or getattr(run, "skipped_deps", ())
        or not collected
        or set(outcomes) != set(collected)
    ):
        return None
    if any(outcomes.get(node) != "skipped" for node in collected) or set(skips) != set(collected):
        return None
    evidence = [skips[node] for node in collected]
    if any(kind != "marker" or reason not in ENV_SKIP_REASONS for kind, reason in evidence):
        return None
    return evidence
