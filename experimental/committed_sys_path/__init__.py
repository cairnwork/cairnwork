"""Committed sys.path experimental capability.

Verifies that in-process search path modifications in candidate commits point
only to base-committed repository directories without shadow modules.
"""

from __future__ import annotations

from pathlib import Path

from experimental.committed_sys_path.git_io import (
    _rev_parse,
    _show_file_bytes,
    load_cand_shadow_extras,
    load_diff_info,
    load_tree_dirs,
)
from experimental.committed_sys_path.source_check import (
    Verdict,
    check_source_impl,
    is_allowlisted,
    is_test_file,
    scan_unchanged_shadows,
)

__all__ = ["Verdict", "check_commit", "check_source", "is_test_file"]


def check_source(
    path: str,
    old_source: bytes | None,
    new_source: bytes,
    committed_dirs: frozenset[str],
    shadow_dirs: frozenset[str] = frozenset(),
    *,
    include_tests: bool = False,
    allowlist_prefixes: tuple[str, ...] = (),
) -> Verdict:
    """Check source modifications for unauthorized in-process search path edits."""
    if not include_tests and is_test_file(path):
        return Verdict("ACCEPT", "SKIPPED_TEST_FILE")
    if is_allowlisted(path, allowlist_prefixes):
        return Verdict("ACCEPT", "ALLOWLISTED_PATH")
    try:
        return check_source_impl(
            path,
            old_source,
            new_source,
            committed_dirs,
            shadow_dirs,
        )
    except (
        SyntaxError,
        ValueError,
        UnicodeDecodeError,
        TypeError,
        RecursionError,
        MemoryError,
    ) as exc:
        return Verdict(
            "ERROR",
            "UNREADABLE_SOURCE",
            ({"error": type(exc).__name__, "path": path},),
        )


def _check_changed_file(
    repo_path: Path,
    base_sha: str,
    cand_sha: str,
    status: str,
    fpath: str,
    committed_dirs: frozenset[str],
    shadow_dirs: frozenset[str],
    cross_shadow_dirs: frozenset[str],
) -> Verdict:
    old_bytes = _show_file_bytes(repo_path, base_sha, fpath) if status != "A" else None
    if status != "A" and old_bytes is None:
        return Verdict(
            "ERROR", "GIT_ERROR", ({"action": "show_file", "ref": base_sha, "file": fpath},)
        )
    new_bytes = _show_file_bytes(repo_path, cand_sha, fpath)
    if new_bytes is None:
        return Verdict(
            "ERROR", "GIT_ERROR", ({"action": "show_file", "ref": cand_sha, "file": fpath},)
        )
    try:
        return check_source_impl(
            fpath,
            old_bytes,
            new_bytes,
            committed_dirs,
            shadow_dirs,
            cross_shadow_dirs=cross_shadow_dirs,
        )
    except (
        SyntaxError,
        ValueError,
        UnicodeDecodeError,
        TypeError,
        RecursionError,
        MemoryError,
    ) as exc:
        return Verdict(
            "ERROR",
            "UNREADABLE_SOURCE",
            ({"error": type(exc).__name__, "path": fpath},),
        )


def check_commit(
    repo: Path | str,
    base: str,
    candidate: str,
    *,
    include_tests: bool = False,
    allowlist_prefixes: tuple[str, ...] = (),
) -> Verdict:
    """Verify in-process search path modifications across a git commit range."""
    repo_path = Path(repo).resolve()
    base_sha = _rev_parse(repo_path, base)
    cand_sha = _rev_parse(repo_path, candidate)
    if not base_sha or not cand_sha:
        return Verdict("ERROR", "INVALID_COMMIT", ({"base": base, "candidate": candidate},))

    committed_dirs = load_tree_dirs(repo_path, base_sha, cand_sha)
    if committed_dirs is None:
        return Verdict("ERROR", "GIT_ERROR", ({"action": "load_tree_dirs", "ref": base_sha},))

    changed_files, shadow_dirs = load_diff_info(repo_path, base_sha, cand_sha)
    if changed_files is None:
        return Verdict(
            "ERROR",
            "GIT_ERROR",
            ({"action": "load_diff_info", "range": f"{base_sha}..{cand_sha}"},),
        )

    cross_shadow_dirs: frozenset[str] = frozenset()
    cand_py_oids: dict[str, str] = {}
    if any(st != "D" for st, _ in changed_files):
        extras = load_cand_shadow_extras(repo_path, base_sha, cand_sha, changed_files)
        if extras is None:
            return Verdict("ERROR", "GIT_ERROR", ({"action": "load_cand_shadow_extras"},))
        cross_shadow_dirs, cand_py_oids = extras

    all_findings: list[dict[str, object]] = []
    checked_paths: set[str] = set()
    for status, fpath in changed_files:
        if not fpath.endswith(".py") or status == "D":
            continue
        checked_paths.add(fpath)
        if (not include_tests and is_test_file(fpath)) or is_allowlisted(fpath, allowlist_prefixes):
            continue
        v = _check_changed_file(
            repo_path,
            base_sha,
            cand_sha,
            status,
            fpath,
            committed_dirs,
            shadow_dirs,
            cross_shadow_dirs,
        )
        if v.verdict == "ERROR":
            return v
        if v.verdict == "REJECT":
            all_findings.extend(v.findings)

    unchanged = scan_unchanged_shadows(
        repo_path,
        cand_py_oids,
        checked_paths,
        committed_dirs,
        shadow_dirs,
        cross_shadow_dirs,
        include_tests=include_tests,
        allowlist_prefixes=allowlist_prefixes,
    )
    if any(f.get("reason") == "GIT_ERROR" for f in unchanged):
        return Verdict("ERROR", "GIT_ERROR", tuple(unchanged))
    all_findings.extend(unchanged)

    if all_findings:
        reason = (
            "SHADOW_IN_COMMITTED_DIR"
            if any(f.get("reason") == "SHADOW_IN_COMMITTED_DIR" for f in all_findings)
            else "NON_COMMITTED_SEARCH_PATH"
        )
        return Verdict("REJECT", reason, tuple(all_findings))
    return Verdict("ACCEPT", "ALL_COMMITTED")
