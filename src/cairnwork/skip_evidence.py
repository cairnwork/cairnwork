"""Recognise environment-only verifier skips without treating candidate skips as evidence."""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from typing import Any

from cairnwork.deferred_tests import BaseUncomparable, Comparison
from cairnwork.gitcmd import GitCommandError, run_git
from cairnwork.textclean import replace_hidden

ENV_SKIP_REASONS = (
    "needs macOS sandbox-exec",
    "needs the cairnwork-verifier account",
)
_DELIMITERS = re.compile(r"[\[\];:]")


def env_skip_comparison(
    runs: tuple[Any, ...] | list[Any], repo: Path, base: str, judged: str
) -> Comparison | None:
    """Return UNCOMPARABLE only when every verifier has trusted all-skipped evidence."""
    changed = _changed_paths(repo, base, judged)
    if changed is None or not runs:
        return None
    details = []
    for run in runs:
        detail = _run_detail(run, changed, repo, base)
        if detail is None:
            return None
        details.append(detail)
    return Comparison("UNCOMPARABLE", "TESTS_SKIPPED_ENVIRONMENT", "; ".join(details))


def raise_for_env_skips(runs, repo: Path, base: str, judged: str) -> None:
    """Raise the verify-boundary signal when every verifier lacks environment evidence."""
    if comparison := env_skip_comparison(runs, repo, base, judged):
        raise BaseUncomparable(comparison)


def _changed_paths(repo: Path, base: str, judged: str) -> set[str] | None:
    try:
        return set(run_git(repo, "diff", "--name-only", "-z", base, judged).split("\0"))
    except GitCommandError:
        return None


def _run_detail(run: Any, changed: set[str], repo: Path, base: str) -> str | None:
    collected: tuple[str, ...] = tuple(getattr(run, "collected", ()))
    outcomes = getattr(run, "outcomes", {})
    skips: dict[str, tuple[str, str]] = getattr(run, "skips", {})
    if not _clean_run(run) or not collected or set(outcomes) != set(collected):
        return None
    if any(outcomes.get(node) != "skipped" for node in collected) or set(skips) != set(collected):
        return None
    evidence = [skips[node] for node in collected]
    if any(kind != "marker" or reason not in ENV_SKIP_REASONS for kind, reason in evidence):
        return None
    files = {_node_file(node) for node in collected}
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
