"""Committed sys.path experimental capability.

Verifies that in-process search path modifications in candidate commits point
only to base-committed repository directories without shadow modules.
"""

from __future__ import annotations

import ast
import collections
import posixpath
import subprocess
from dataclasses import dataclass
from pathlib import Path

from experimental.committed_sys_path.evaluator import (
    PathEdit,
    StaticPathEvaluator,
    collect_aliases_and_env,
    find_path_edits,
)


@dataclass(frozen=True)
class Verdict:
    """Represents the verdict of committed_sys_path verification."""

    verdict: str  # "ACCEPT" | "REJECT" | "ERROR"
    reason: str
    findings: tuple[dict[str, object], ...] = ()


def is_test_file(path: str) -> bool:
    """Check if the given path belongs to test suites or test configuration."""
    norm = posixpath.normpath(path)
    base = posixpath.basename(norm)
    if norm.startswith("tests/") or "/tests/" in norm or norm == "tests":
        return True
    if base.startswith("test_") or base.endswith("_test.py"):
        return True
    if base == "conftest.py":
        return True
    return False


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

    if any(path.startswith(prefix) for prefix in allowlist_prefixes):
        return Verdict("ACCEPT", "ALLOWLISTED_PATH")

    try:
        new_text = new_source.decode("utf-8")
        new_tree = ast.parse(new_text, filename=path)
    except (UnicodeDecodeError, SyntaxError) as exc:
        return Verdict("ERROR", "UNREADABLE_SOURCE", ({"error": str(exc), "path": path},))

    old_tree: ast.AST | None = None
    old_text: str = ""
    if old_source is not None:
        try:
            old_text = old_source.decode("utf-8")
            old_tree = ast.parse(old_text, filename=path)
        except (UnicodeDecodeError, SyntaxError):
            old_tree = None

    new_edits = find_path_edits(new_tree, new_text)
    old_edits = find_path_edits(old_tree, old_text) if old_tree else []

    candidate_edits = _diff_path_edits(old_edits, new_edits)
    if not candidate_edits:
        return Verdict("ACCEPT", "NO_NEW_PATH_EDITS")

    _, _, _, _, env = collect_aliases_and_env(new_tree)
    evaluator = StaticPathEvaluator(env, path)
    findings = _evaluate_candidate_edits(candidate_edits, evaluator, committed_dirs, shadow_dirs)

    if not findings:
        return Verdict("ACCEPT", "COMMITTED_SEARCH_PATH")

    if any(f.get("reason") == "SHADOW_IN_COMMITTED_DIR" for f in findings):
        return Verdict("REJECT", "SHADOW_IN_COMMITTED_DIR", tuple(findings))
    return Verdict("REJECT", "NON_COMMITTED_SEARCH_PATH", tuple(findings))


def _diff_path_edits(old_edits: list[PathEdit], new_edits: list[PathEdit]) -> list[PathEdit]:
    def edit_key(e: PathEdit) -> tuple[str, tuple[str, ...]]:
        return (e.kind, tuple(ast.unparse(t) for t in e.target_nodes))

    old_counts = collections.Counter(edit_key(e) for e in old_edits)
    candidate_edits: list[PathEdit] = []
    for e in new_edits:
        k = edit_key(e)
        if old_counts[k] > 0:
            old_counts[k] -= 1
        else:
            candidate_edits.append(e)
    return candidate_edits


def _evaluate_candidate_edits(
    candidate_edits: list[PathEdit],
    evaluator: StaticPathEvaluator,
    committed_dirs: frozenset[str],
    shadow_dirs: frozenset[str],
) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    for edit in candidate_edits:
        if not edit.target_nodes:
            findings.append(
                {
                    "line": edit.lineno,
                    "kind": edit.kind,
                    "reason": "NON_COMMITTED_SEARCH_PATH",
                    "code": edit.raw_stmt,
                }
            )
            continue
        for target_node in edit.target_nodes:
            finding = _check_target_node(edit, target_node, evaluator, committed_dirs, shadow_dirs)
            if finding is not None:
                findings.append(finding)
    return findings


def _check_target_node(
    edit: PathEdit,
    target_node: ast.AST,
    evaluator: StaticPathEvaluator,
    committed_dirs: frozenset[str],
    shadow_dirs: frozenset[str],
) -> dict[str, object] | None:
    eval_res = evaluator.evaluate(target_node)
    target_code = ast.unparse(target_node)
    if eval_res is None:
        return {
            "line": edit.lineno,
            "kind": edit.kind,
            "target": target_code,
            "reason": "NON_COMMITTED_SEARCH_PATH",
            "code": edit.raw_stmt,
        }
    repo_dir = evaluator.resolve_to_repo_dir(eval_res)
    if repo_dir is None:
        return {
            "line": edit.lineno,
            "kind": edit.kind,
            "target": target_code,
            "reason": "NON_COMMITTED_SEARCH_PATH",
            "code": edit.raw_stmt,
        }
    norm_dir = "" if repo_dir in (".", "") else repo_dir
    is_committed = norm_dir in committed_dirs or (
        norm_dir == "" and ("" in committed_dirs or "." in committed_dirs)
    )
    if not is_committed:
        return {
            "line": edit.lineno,
            "kind": edit.kind,
            "dir": repo_dir,
            "reason": "NON_COMMITTED_SEARCH_PATH",
            "code": edit.raw_stmt,
        }
    is_shadow = any(norm_dir == ("" if s in (".", "") else s) for s in shadow_dirs)
    if is_shadow:
        return {
            "line": edit.lineno,
            "kind": edit.kind,
            "dir": repo_dir,
            "reason": "SHADOW_IN_COMMITTED_DIR",
            "code": edit.raw_stmt,
        }
    return None


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

    committed_dirs = _load_committed_dirs(repo_path, base_sha)
    if committed_dirs is None:
        return Verdict("ERROR", "GIT_ERROR", ({"action": "load_committed_dirs", "ref": base_sha},))

    changed_files, shadow_dirs = _load_diff_info(repo_path, base_sha, cand_sha)
    if changed_files is None:
        return Verdict(
            "ERROR",
            "GIT_ERROR",
            ({"action": "load_diff_info", "range": f"{base_sha}..{cand_sha}"},),
        )

    all_findings: list[dict[str, object]] = []
    for status, fpath in changed_files:
        if not fpath.endswith(".py") or status == "D":
            continue
        old_bytes = _show_file_bytes(repo_path, base_sha, fpath) if status != "A" else None
        new_bytes = _show_file_bytes(repo_path, cand_sha, fpath)
        if new_bytes is None:
            return Verdict(
                "ERROR", "GIT_ERROR", ({"action": "show_file", "ref": cand_sha, "file": fpath},)
            )
        v = check_source(
            fpath,
            old_bytes,
            new_bytes,
            committed_dirs,
            shadow_dirs,
            include_tests=include_tests,
            allowlist_prefixes=allowlist_prefixes,
        )
        if v.verdict == "ERROR":
            return v
        if v.verdict == "REJECT":
            all_findings.extend(v.findings)

    if all_findings:
        reason = (
            "SHADOW_IN_COMMITTED_DIR"
            if any(f.get("reason") == "SHADOW_IN_COMMITTED_DIR" for f in all_findings)
            else "NON_COMMITTED_SEARCH_PATH"
        )
        return Verdict("REJECT", reason, tuple(all_findings))
    return Verdict("ACCEPT", "ALL_COMMITTED")


def _is_hex_oid(ref: str) -> bool:
    return len(ref) == 40 and all(c in "0123456789abcdefABCDEF" for c in ref)


def _rev_parse(repo: Path, ref: str) -> str | None:
    if not ref or ref.startswith("-"):
        return None
    p = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}"],
        capture_output=True,
        text=True,
    )
    oid = p.stdout.strip()
    return oid if p.returncode == 0 and _is_hex_oid(oid) else None


def _load_committed_dirs(repo: Path, ref: str) -> frozenset[str] | None:
    if not _is_hex_oid(ref):
        return None
    p = subprocess.run(
        ["git", "-C", str(repo), "ls-tree", "-r", "--name-only", ref],
        capture_output=True,
        text=True,
        errors="replace",
    )
    if p.returncode != 0:
        return None
    dirs = {"", "."}
    for f in p.stdout.splitlines():
        d = posixpath.dirname(f)
        while d and d not in dirs:
            dirs.add(d)
            d = posixpath.dirname(d)
    return frozenset(dirs)


def _load_diff_info(
    repo: Path, base: str, candidate: str
) -> tuple[list[tuple[str, str]] | None, frozenset[str]]:
    if not _is_hex_oid(base) or not _is_hex_oid(candidate):
        return None, frozenset()
    p = subprocess.run(
        ["git", "-C", str(repo), "diff", "--name-status", "--no-renames", base, candidate, "--"],
        capture_output=True,
        text=True,
        errors="replace",
    )
    if p.returncode != 0:
        return None, frozenset()
    changed_files: list[tuple[str, str]] = []
    shadow_dirs: set[str] = set()
    for line in p.stdout.splitlines():
        parts = line.split("\t", 1)
        if len(parts) == 2:
            status, fpath = parts[0].strip(), parts[1].strip()
            changed_files.append((status, fpath))
            if fpath.endswith(".py") and status in ("A", "M"):
                shadow_dirs.add(posixpath.dirname(fpath))
    return changed_files, frozenset(shadow_dirs)


def _show_file_bytes(repo: Path, ref: str, fpath: str) -> bytes | None:
    if not _is_hex_oid(ref) or not fpath or fpath.startswith("-"):
        return None
    p = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "-p", f"{ref}:{fpath}"], capture_output=True
    )
    return p.stdout if p.returncode == 0 else None
