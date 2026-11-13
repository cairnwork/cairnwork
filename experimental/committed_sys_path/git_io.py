"""Git NUL-delimited (-z) I/O helpers for committed_sys_path."""

from __future__ import annotations

import posixpath
import subprocess
from pathlib import Path


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


def _show_file_bytes(repo: Path, ref: str, fpath: str) -> bytes | None:
    if not _is_hex_oid(ref) or not fpath or fpath.startswith("-"):
        return None
    p = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "-p", f"{ref}:{fpath}"],
        capture_output=True,
    )
    return p.stdout if p.returncode == 0 else None


def _parse_ls_tree_z(raw: bytes) -> tuple[set[str], set[str], set[str]]:
    """Return (file_dirs, tree_dirs, symlink_paths) from `git ls-tree -r -t -z`."""
    file_dirs: set[str] = {"", "."}
    tree_dirs: set[str] = {"", "."}
    symlinks: set[str] = set()
    for entry in raw.split(b"\x00"):
        if not entry or b"\t" not in entry:
            continue
        meta, path_b = entry.split(b"\t", 1)
        fpath = path_b.decode("utf-8", errors="surrogateescape")
        mode = meta.split(b" ", 1)[0]
        if mode == b"040000":
            tree_dirs.add(fpath)
        elif mode == b"120000":
            symlinks.add(fpath)
        else:
            d = posixpath.dirname(fpath)
            while d and d not in file_dirs:
                file_dirs.add(d)
                d = posixpath.dirname(d)
    return file_dirs, tree_dirs, symlinks


def _has_symlink_prefix(d: str, symlinks: set[str]) -> bool:
    if not d or d == ".":
        return False
    cur = d
    while cur:
        if cur in symlinks:
            return True
        cur = posixpath.dirname(cur)
    return False


def load_tree_dirs(repo: Path, base_sha: str, cand_sha: str) -> frozenset[str] | None:
    """Load base committed dirs, excluding dirs missing or symlinked in candidate."""
    if not _is_hex_oid(base_sha) or not _is_hex_oid(cand_sha):
        return None
    p_base = subprocess.run(
        ["git", "-C", str(repo), "ls-tree", "-r", "-t", "-z", base_sha],
        capture_output=True,
    )
    p_cand = subprocess.run(
        ["git", "-C", str(repo), "ls-tree", "-r", "-t", "-z", cand_sha],
        capture_output=True,
    )
    if p_base.returncode != 0 or p_cand.returncode != 0:
        return None
    base_dirs, _, _ = _parse_ls_tree_z(p_base.stdout)
    _, cand_trees, cand_symlinks = _parse_ls_tree_z(p_cand.stdout)
    valid = {"", "."}
    for d in base_dirs:
        if d in ("", "."):
            continue
        if d in cand_trees and not _has_symlink_prefix(d, cand_symlinks):
            valid.add(d)
    return frozenset(valid)


def load_diff_info(
    repo: Path, base_sha: str, cand_sha: str
) -> tuple[list[tuple[str, str]] | None, frozenset[str]]:
    """Load changed files and all candidate-modified directories (including ancestors)."""
    if not _is_hex_oid(base_sha) or not _is_hex_oid(cand_sha):
        return None, frozenset()
    p = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "diff",
            "--name-status",
            "--no-renames",
            "-z",
            base_sha,
            cand_sha,
            "--",
        ],
        capture_output=True,
    )
    if p.returncode != 0:
        return None, frozenset()
    tokens = p.stdout.split(b"\x00")
    changed_files: list[tuple[str, str]] = []
    shadow_dirs: set[str] = set()
    idx = 0
    while idx + 1 < len(tokens):
        status_b = tokens[idx]
        if not status_b:
            idx += 1
            continue
        fpath_b = tokens[idx + 1]
        idx += 2
        status = status_b.decode("utf-8", errors="replace")
        fpath = fpath_b.decode("utf-8", errors="surrogateescape")
        changed_files.append((status, fpath))
        if status != "D":
            d = posixpath.dirname(fpath)
            while True:
                shadow_dirs.add(d)
                if not d:
                    break
                d = posixpath.dirname(d)
    return changed_files, frozenset(shadow_dirs)
