"""Source AST checking, semantic edit diffing, and shadow evaluation helpers."""

from __future__ import annotations

import ast
import collections
import copy
import posixpath
from dataclasses import dataclass
from pathlib import Path

from experimental.committed_sys_path.evaluator import (
    PathEdit,
    StaticPathEvaluator,
    collect_aliases_and_env,
    find_path_edits,
)
from experimental.committed_sys_path.git_io import _batch_cat_file

_MAX_AST_DEPTH = 500
_UNCHANGED_PREFILTER = (
    b"sys",
    b"addsitedir",
    b"__path__",
    b"submodule_search_locations",
    b"PYTHONPATH",
    b"environ",
    b"putenv",
)


@dataclass(frozen=True)
class Verdict:
    """Represents the verdict of committed_sys_path verification."""

    verdict: str  # "ACCEPT" | "REJECT" | "ERROR"
    reason: str
    findings: tuple[dict[str, object], ...] = ()


def norm_repo_path(path: str) -> str:
    """Normalize a repository-relative path without stripping leading dots from hidden names."""
    norm = posixpath.normpath(path)
    while norm.startswith("./"):
        norm = norm[2:]
    return norm.lstrip("/")


def is_test_file(path: str) -> bool:
    """Check if the given path is under top-level tests/ or is conftest.py."""
    norm = norm_repo_path(path)
    return norm == "tests" or norm.startswith("tests/") or norm == "conftest.py"


def is_allowlisted(path: str, prefixes: tuple[str, ...]) -> bool:
    """Check whether path falls under any segment-aligned allowlist prefix."""
    norm = norm_repo_path(path)
    for p in prefixes:
        clean = norm_repo_path(p).strip("/")
        if clean and (norm == clean or norm.startswith(clean + "/")):
            return True
    return False


def _exceeds_ast_depth(tree: ast.AST, limit: int = _MAX_AST_DEPTH) -> bool:
    stack: list[tuple[ast.AST, int]] = [(tree, 0)]
    while stack:
        node, depth = stack.pop()
        if depth > limit:
            return True
        for child in ast.iter_child_nodes(node):
            stack.append((child, depth + 1))
    return False


def parse_source(raw: bytes, path: str) -> ast.AST | None:
    """Parse Python bytes and enforce bounded AST nesting depth."""
    try:
        tree = ast.parse(raw, filename=path)
    except (SyntaxError, ValueError, UnicodeDecodeError, TypeError, RecursionError, MemoryError):
        return None
    if _exceeds_ast_depth(tree):
        return None
    return tree


def _inline_env_expr(node: ast.AST, env: dict[str, ast.AST], depth: int = 0) -> ast.AST:
    if depth > 8:
        return node
    if isinstance(node, ast.Name) and node.id in env:
        return _inline_env_expr(env[node.id], env, depth + 1)
    cloned = copy.copy(node)
    for field, val in ast.iter_fields(cloned):
        if isinstance(val, ast.AST):
            setattr(cloned, field, _inline_env_expr(val, env, depth + 1))
        elif isinstance(val, list):
            setattr(
                cloned,
                field,
                [
                    _inline_env_expr(item, env, depth + 1) if isinstance(item, ast.AST) else item
                    for item in val
                ],
            )
    return cloned


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


def _edit_key(edit: PathEdit, evaluator: StaticPathEvaluator) -> tuple[object, ...]:
    dirs = _resolve_edit_dirs(edit, evaluator)
    if dirs is not None:
        return (edit.kind, "resolved", dirs)
    if edit.target_nodes:
        dumps = tuple(
            ast.dump(_inline_env_expr(n, evaluator.st.env), include_attributes=False)
            for n in edit.target_nodes
        )
        return (edit.kind, "expr", dumps)
    return (edit.kind, "stmt", edit.raw_stmt)


def diff_path_edits(
    old_edits: list[PathEdit],
    new_edits: list[PathEdit],
    old_eval: StaticPathEvaluator | None,
    new_eval: StaticPathEvaluator,
) -> list[PathEdit]:
    """Return newly added or semantically modified edits between old and new versions."""
    old_counts: collections.Counter[tuple[object, ...]] = collections.Counter()
    if old_eval is not None:
        for e in old_edits:
            old_counts[_edit_key(e, old_eval)] += 1
    candidate_edits: list[PathEdit] = []
    for e in new_edits:
        k = _edit_key(e, new_eval)
        if old_counts[k] > 0:
            old_counts[k] -= 1
        else:
            candidate_edits.append(e)
    return candidate_edits


def _norm_dir(d: str) -> str:
    return "" if d in (".", "") else d


def _is_dir_committed(d: str, committed_dirs: frozenset[str]) -> bool:
    nd = _norm_dir(d)
    return nd in committed_dirs or (nd == "" and ("" in committed_dirs or "." in committed_dirs))


def _is_shadow_hit(
    norm_dir: str,
    caller_dir: str,
    shadow_dirs: frozenset[str],
    cross_shadow_dirs: frozenset[str],
) -> bool:
    if any(norm_dir == _norm_dir(s) for s in shadow_dirs):
        return True
    return norm_dir != caller_dir and any(norm_dir == _norm_dir(s) for s in cross_shadow_dirs)


def _reject_finding(edit: PathEdit, reason: str, **extra: object) -> dict[str, object]:
    finding: dict[str, object] = {
        "line": edit.lineno,
        "kind": edit.kind,
        "reason": reason,
        "code": edit.raw_stmt,
    }
    finding.update(extra)
    return finding


def _check_target_node(
    edit: PathEdit,
    target_node: ast.AST,
    evaluator: StaticPathEvaluator,
    committed_dirs: frozenset[str],
    shadow_dirs: frozenset[str],
    *,
    caller_dir: str = "",
    cross_shadow_dirs: frozenset[str] = frozenset(),
) -> dict[str, object] | None:
    eval_res = evaluator.evaluate(target_node)
    target_code = ast.unparse(target_node)
    if eval_res is None:
        return _reject_finding(edit, "NON_COMMITTED_SEARCH_PATH", target=target_code)
    repo_dir = evaluator.resolve_to_repo_dir(eval_res)
    if repo_dir is None:
        return _reject_finding(edit, "NON_COMMITTED_SEARCH_PATH", target=target_code)
    for vdir in (*eval_res.visited_dirs, repo_dir):
        if not _is_dir_committed(vdir, committed_dirs):
            return _reject_finding(edit, "NON_COMMITTED_SEARCH_PATH", dir=vdir)
    norm_dir = _norm_dir(repo_dir)
    if _is_shadow_hit(norm_dir, caller_dir, shadow_dirs, cross_shadow_dirs):
        return _reject_finding(edit, "SHADOW_IN_COMMITTED_DIR", dir=repo_dir)
    return None


def _evaluate_candidate_edits(
    candidate_edits: list[PathEdit],
    evaluator: StaticPathEvaluator,
    committed_dirs: frozenset[str],
    shadow_dirs: frozenset[str],
    *,
    caller_dir: str = "",
    cross_shadow_dirs: frozenset[str] = frozenset(),
) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    for edit in candidate_edits:
        if not edit.target_nodes:
            findings.append(_reject_finding(edit, "NON_COMMITTED_SEARCH_PATH"))
            continue
        for target_node in edit.target_nodes:
            finding = _check_target_node(
                edit,
                target_node,
                evaluator,
                committed_dirs,
                shadow_dirs,
                caller_dir=caller_dir,
                cross_shadow_dirs=cross_shadow_dirs,
            )
            if finding is not None:
                findings.append(finding)
    return findings


def _collect_shadow_only_findings(
    new_edits: list[PathEdit],
    evaluator: StaticPathEvaluator,
    committed_dirs: frozenset[str],
    shadow_dirs: frozenset[str],
    *,
    caller_dir: str,
    cross_shadow_dirs: frozenset[str],
) -> list[dict[str, object]]:
    if not (shadow_dirs or cross_shadow_dirs):
        return []
    findings: list[dict[str, object]] = []
    for edit in new_edits:
        for target_node in edit.target_nodes:
            f = _check_target_node(
                edit,
                target_node,
                evaluator,
                committed_dirs,
                shadow_dirs,
                caller_dir=caller_dir,
                cross_shadow_dirs=cross_shadow_dirs,
            )
            if f is not None and f.get("reason") == "SHADOW_IN_COMMITTED_DIR":
                findings.append(f)
    return findings


def check_source_impl(
    path: str,
    old_source: bytes | None,
    new_source: bytes,
    committed_dirs: frozenset[str],
    shadow_dirs: frozenset[str],
    *,
    cross_shadow_dirs: frozenset[str] = frozenset(),
) -> Verdict:
    """Core AST check for a single source file with optional cross-dir shadow context."""
    new_tree = parse_source(new_source, path)
    if new_tree is None:
        return Verdict("ERROR", "UNREADABLE_SOURCE", ({"error": "unparseable", "path": path},))
    new_text = new_source.decode("utf-8", errors="replace")

    old_tree: ast.AST | None = None
    old_text = ""
    if old_source is not None:
        old_tree = parse_source(old_source, path)
        if old_tree is not None:
            old_text = old_source.decode("utf-8", errors="replace")

    new_edits = find_path_edits(new_tree, new_text)
    old_edits = find_path_edits(old_tree, old_text) if old_tree else []
    new_eval = StaticPathEvaluator(collect_aliases_and_env(new_tree), path)
    old_eval = StaticPathEvaluator(collect_aliases_and_env(old_tree), path) if old_tree else None

    caller_dir = _norm_dir(posixpath.dirname(norm_repo_path(path)))
    candidate_edits = diff_path_edits(old_edits, new_edits, old_eval, new_eval)
    findings = _evaluate_candidate_edits(
        candidate_edits,
        new_eval,
        committed_dirs,
        shadow_dirs,
        caller_dir=caller_dir,
        cross_shadow_dirs=cross_shadow_dirs,
    )
    if not findings and (shadow_dirs or cross_shadow_dirs):
        findings = _collect_shadow_only_findings(
            new_edits,
            new_eval,
            committed_dirs,
            shadow_dirs,
            caller_dir=caller_dir,
            cross_shadow_dirs=cross_shadow_dirs,
        )
    if not findings:
        reason = "COMMITTED_SEARCH_PATH" if candidate_edits else "NO_NEW_PATH_EDITS"
        return Verdict("ACCEPT", reason)
    if any(f.get("reason") == "SHADOW_IN_COMMITTED_DIR" for f in findings):
        return Verdict("REJECT", "SHADOW_IN_COMMITTED_DIR", tuple(findings))
    return Verdict("REJECT", "NON_COMMITTED_SEARCH_PATH", tuple(findings))


def scan_unchanged_shadows(
    repo_path: Path,
    cand_py_oids: dict[str, str],
    checked_paths: set[str],
    committed_dirs: frozenset[str],
    shadow_dirs: frozenset[str],
    cross_shadow_dirs: frozenset[str],
    *,
    include_tests: bool,
    allowlist_prefixes: tuple[str, ...],
) -> list[dict[str, object]]:
    """Inspect unchanged candidate .py files whose search paths now resolve to shadow_dirs."""
    if not (shadow_dirs or cross_shadow_dirs):
        return []
    targets = [
        (fp, oid)
        for fp, oid in cand_py_oids.items()
        if fp not in checked_paths
        and (include_tests or not is_test_file(fp))
        and not is_allowlisted(fp, allowlist_prefixes)
    ]
    if not targets:
        return []
    blobs = _batch_cat_file(repo_path, [oid for _, oid in targets])
    if blobs is None:
        return [{"reason": "GIT_ERROR", "error": "batch_read_failed"}]
    findings: list[dict[str, object]] = []
    for fp, oid in targets:
        raw = blobs.get(oid, b"")
        if not any(tok in raw for tok in _UNCHANGED_PREFILTER):
            continue
        tree = parse_source(raw, fp)
        if tree is None:
            continue
        text = raw.decode("utf-8", errors="replace")
        edits = find_path_edits(tree, text)
        ev = StaticPathEvaluator(collect_aliases_and_env(tree), fp)
        caller_dir = _norm_dir(posixpath.dirname(norm_repo_path(fp)))
        findings.extend(
            _collect_shadow_only_findings(
                edits,
                ev,
                committed_dirs,
                shadow_dirs,
                caller_dir=caller_dir,
                cross_shadow_dirs=cross_shadow_dirs,
            )
        )
    return findings
