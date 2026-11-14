"""Committed sys.path experimental capability.

Verifies that in-process search path modifications in candidate commits point
only to base-committed repository directories without shadow modules.
"""

from __future__ import annotations

import ast
import collections
import posixpath
from dataclasses import dataclass
from pathlib import Path

from experimental.committed_sys_path.evaluator import (
    PathEdit,
    StaticPathEvaluator,
    collect_aliases_and_env,
    find_path_edits,
)
from experimental.committed_sys_path.git_io import (
    _rev_parse,
    _show_file_bytes,
    load_diff_info,
    load_tree_dirs,
)


@dataclass(frozen=True)
class Verdict:
    """Represents the verdict of committed_sys_path verification."""

    verdict: str  # "ACCEPT" | "REJECT" | "ERROR"
    reason: str
    findings: tuple[dict[str, object], ...] = ()


def is_test_file(path: str) -> bool:
    """Check if the given path is under top-level tests/ or is conftest.py."""
    norm = posixpath.normpath(path).lstrip("./")
    return norm == "tests" or norm.startswith("tests/") or norm == "conftest.py"


def _is_allowlisted(path: str, prefixes: tuple[str, ...]) -> bool:
    norm = posixpath.normpath(path).lstrip("./")
    for p in prefixes:
        clean = posixpath.normpath(p).strip("/")
        if clean and (norm == clean or norm.startswith(clean + "/")):
            return True
    return False


def _parse_source(raw: bytes, path: str) -> ast.AST | None:
    try:
        return ast.parse(raw, filename=path)
    except (SyntaxError, ValueError, UnicodeDecodeError, TypeError):
        return None


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

    if _is_allowlisted(path, allowlist_prefixes):
        return Verdict("ACCEPT", "ALLOWLISTED_PATH")

    new_tree = _parse_source(new_source, path)
    if new_tree is None:
        return Verdict("ERROR", "UNREADABLE_SOURCE", ({"error": "unparseable", "path": path},))
    new_text = new_source.decode("utf-8", errors="replace")

    old_tree: ast.AST | None = None
    old_text = ""
    if old_source is not None:
        old_tree = _parse_source(old_source, path)
        old_text = old_source.decode("utf-8", errors="replace")

    new_edits = find_path_edits(new_tree, new_text)
    old_edits = find_path_edits(old_tree, old_text) if old_tree else []

    new_st = collect_aliases_and_env(new_tree)
    new_eval = StaticPathEvaluator(new_st, path)
    old_eval = StaticPathEvaluator(collect_aliases_and_env(old_tree), path) if old_tree else None

    candidate_edits = _diff_path_edits(old_edits, new_edits, old_eval, new_eval)
    if not candidate_edits:
        return Verdict("ACCEPT", "NO_NEW_PATH_EDITS")

    findings = _evaluate_candidate_edits(candidate_edits, new_eval, committed_dirs, shadow_dirs)
    if not findings:
        return Verdict("ACCEPT", "COMMITTED_SEARCH_PATH")

    if any(f.get("reason") == "SHADOW_IN_COMMITTED_DIR" for f in findings):
        return Verdict("REJECT", "SHADOW_IN_COMMITTED_DIR", tuple(findings))
    return Verdict("REJECT", "NON_COMMITTED_SEARCH_PATH", tuple(findings))


def _resolve_edit_dirs(edit: PathEdit, evaluator: StaticPathEvaluator) -> tuple[str, ...] | None:
    if not edit.target_nodes:
        return None
    dirs: list[str] = []
    for node in edit.target_nodes:
        ev = evaluator.evaluate(node)
        if ev is None:
            return None
        repo_dir = evaluator.resolve_to_repo_dir(ev)
        if repo_dir is None:
            return None
        dirs.append(repo_dir)
    return tuple(dirs)


def _diff_path_edits(
    old_edits: list[PathEdit],
    new_edits: list[PathEdit],
    old_eval: StaticPathEvaluator | None,
    new_eval: StaticPathEvaluator,
) -> list[PathEdit]:
    old_counts: collections.Counter[tuple[str, tuple[str, ...]]] = collections.Counter()
    if old_eval is not None:
        for e in old_edits:
            dirs = _resolve_edit_dirs(e, old_eval)
            if dirs is not None:
                old_counts[(e.kind, dirs)] += 1

    candidate_edits: list[PathEdit] = []
    for e in new_edits:
        dirs = _resolve_edit_dirs(e, new_eval)
        if dirs is not None and old_counts[(e.kind, dirs)] > 0:
            old_counts[(e.kind, dirs)] -= 1
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
