"""Git NUL-delimited (-z) I/O and shadow-module analysis helpers for committed_sys_path."""

from __future__ import annotations

import ast
import posixpath
import subprocess
import sys
from pathlib import Path

_BINARY_SHADOW_SUFFIXES = (".pyc", ".pyo", ".so", ".pyd", ".dylib", ".pth")
_PYTHON_SUFFIXES = (".py", ".pyw")
_MODULE_SUFFIXES = _PYTHON_SUFFIXES + (".pyc", ".pyo", ".so", ".pyd", ".dylib")
_PROTECTED_MODULES = frozenset(
    {
        "pytest",
        "_pytest",
        "pluggy",
        "conftest",
        "py",
        "site",
        "sitecustomize",
        "usercustomize",
        "cairnwork",
        "cairnwork_protocol",
        "cairnwork_sandbox",
        "cairnwork_quickstart",
        "cairnwork_attest",
        "tests",
        "experimental",
    }
)
_STDLIB_NAMES = frozenset(sys.stdlib_module_names)
_STDLIB_NAMES_CF = frozenset(n.casefold() for n in _STDLIB_NAMES)
_PROTECTED_CF = frozenset(n.casefold() for n in _PROTECTED_MODULES)


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


def _parse_ls_tree_z(raw: bytes) -> tuple[set[str], set[str], set[str], dict[str, str]]:
    """Return (file_dirs, tree_dirs, symlink_paths, file_oids) from `git ls-tree -r -t -z`."""
    file_dirs: set[str] = {"", "."}
    tree_dirs: set[str] = {"", "."}
    symlinks: set[str] = set()
    file_oids: dict[str, str] = {}
    for entry in raw.split(b"\x00"):
        if not entry or b"\t" not in entry:
            continue
        meta, path_b = entry.split(b"\t", 1)
        fpath = path_b.decode("utf-8", errors="surrogateescape")
        parts = meta.split(b" ")
        mode = parts[0]
        if mode == b"040000":
            tree_dirs.add(fpath)
        elif mode == b"120000":
            symlinks.add(fpath)
        else:
            if len(parts) >= 3:
                file_oids[fpath] = parts[2].decode("ascii", errors="ignore")
            d = posixpath.dirname(fpath)
            while d and d not in file_dirs:
                file_dirs.add(d)
                d = posixpath.dirname(d)
    return file_dirs, tree_dirs, symlinks, file_oids


def _has_symlink_prefix(d: str, symlinks: set[str]) -> bool:
    if not d or d == ".":
        return False
    cur = d
    while cur:
        if cur in symlinks:
            return True
        cur = posixpath.dirname(cur)
    return False


def _ls_tree(
    repo: Path, ref_sha: str
) -> tuple[set[str], set[str], set[str], dict[str, str]] | None:
    if not _is_hex_oid(ref_sha):
        return None
    p = subprocess.run(
        ["git", "-C", str(repo), "ls-tree", "-r", "-t", "-z", ref_sha],
        capture_output=True,
    )
    if p.returncode != 0:
        return None
    return _parse_ls_tree_z(p.stdout)


def load_tree_dirs(repo: Path, base_sha: str, cand_sha: str) -> frozenset[str] | None:
    """Load base committed dirs, excluding dirs missing or symlinked in base/candidate."""
    base_res = _ls_tree(repo, base_sha)
    cand_res = _ls_tree(repo, cand_sha)
    if base_res is None or cand_res is None:
        return None
    base_dirs, base_trees, base_symlinks, _ = base_res
    _, cand_trees, cand_symlinks, _ = cand_res
    valid = {"", "."}
    for d in base_dirs:
        if d in ("", "."):
            continue
        if (
            d in base_trees
            and d in cand_trees
            and not _has_symlink_prefix(d, base_symlinks)
            and not _has_symlink_prefix(d, cand_symlinks)
        ):
            valid.add(d)
    return frozenset(valid)


def _batch_cat_file(repo: Path, oids: list[str]) -> dict[str, bytes] | None:
    if not oids:
        return {}
    unique = list(dict.fromkeys(oids))
    if any(not _is_hex_oid(o) for o in unique):
        return None
    p = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "--batch"],
        input=("".join(f"{o}\n" for o in unique)).encode("ascii"),
        capture_output=True,
    )
    if p.returncode != 0:
        return None
    raw = p.stdout
    out: dict[str, bytes] = {}
    at = 0
    for oid in unique:
        nl = raw.find(b"\n", at)
        if nl < 0:
            return None
        head = raw[at:nl].split(b" ")
        if len(head) != 3 or head[1] != b"blob":
            return None
        size = int(head[2])
        end = nl + 1 + size
        if end > len(raw):
            return None
        out[oid] = raw[nl + 1 : end]
        at = end + 1
    return out


def _extract_imports(raw: bytes) -> set[str]:
    try:
        tree = ast.parse(raw)
    except (SyntaxError, ValueError, UnicodeDecodeError, TypeError, RecursionError, MemoryError):
        return set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


def _collect_base_stems(base_files: dict[str, str], base_dirs: set[str]) -> set[str]:
    stems: set[str] = set()
    for fpath in base_files:
        low = fpath.lower()
        if not low.endswith(_MODULE_SUFFIXES):
            continue
        base_name = posixpath.basename(fpath)
        stem = (
            base_name.split(".", 1)[0]
            if low.endswith((".so", ".pyd", ".dylib"))
            else base_name.rsplit(".", 1)[0]
        )
        if stem == "__init__":
            pkg = posixpath.basename(posixpath.dirname(fpath))
            if pkg:
                stems.add(pkg)
        elif stem:
            stems.add(stem)
    for d in base_dirs:
        if d and d != ".":
            stems.add(posixpath.basename(d))
    return stems


def _exposed_module_pairs(fpath: str, base_dirs: set[str]) -> list[tuple[str, str]]:
    """Return (exposure_dir, top_level_name) pairs exposed by adding fpath."""
    d = posixpath.dirname(fpath)
    stem = posixpath.basename(fpath).rsplit(".", 1)[0]
    pairs: list[tuple[str, str]] = []
    if stem != "__init__":
        pairs.append((d, stem))
    cur = d
    while cur:
        if cur in base_dirs:
            break
        parent = posixpath.dirname(cur)
        pairs.append((parent, posixpath.basename(cur)))
        cur = parent
    return pairs


def _collect_base_imports(repo: Path, base_files: dict[str, str]) -> set[str] | None:
    py_oids = [oid for fp, oid in base_files.items() if fp.lower().endswith(_PYTHON_SUFFIXES)]
    blobs = _batch_cat_file(repo, py_oids)
    if blobs is None:
        return None
    base_imports: set[str] = set()
    for raw in blobs.values():
        base_imports.update(_extract_imports(raw))
    return base_imports


def _mark_binary_shadow(fpath: str, cand_files: dict[str, str], shadow_dirs: set[str]) -> None:
    d = posixpath.dirname(fpath)
    shadow_dirs.add(d)
    if posixpath.basename(d) == "__pycache__":
        d = posixpath.dirname(d)
        shadow_dirs.add(d)
    while d and f"{d}/__init__.py" in cand_files:
        d = posixpath.dirname(d)
        shadow_dirs.add(d)


def _compute_shadows(
    repo: Path,
    base_sha: str,
    cand_sha: str,
    changed_files: list[tuple[str, str]],
) -> tuple[frozenset[str], frozenset[str], dict[str, str]] | None:
    if not any(s != "D" for s, _ in changed_files):
        return frozenset(), frozenset(), {}
    base_res = _ls_tree(repo, base_sha)
    cand_res = _ls_tree(repo, cand_sha)
    if base_res is None or cand_res is None:
        return None
    base_dirs, _, _, base_files = base_res
    _, _, _, cand_files = cand_res
    cand_py_oids = {
        fp: oid for fp, oid in cand_files.items() if fp.lower().endswith(_PYTHON_SUFFIXES)
    }

    shadow_dirs: set[str] = set()
    added_module_dirs: set[str] = set()
    added_py = [
        fp for st, fp in changed_files if st == "A" and fp.lower().endswith(_PYTHON_SUFFIXES)
    ]
    for st, fpath in changed_files:
        if st != "D" and fpath.lower().endswith(_BINARY_SHADOW_SUFFIXES):
            _mark_binary_shadow(fpath, cand_files, shadow_dirs)

    if not added_py:
        return frozenset(shadow_dirs), frozenset(), cand_py_oids

    base_imports = _collect_base_imports(repo, base_files)
    if base_imports is None:
        return None
    base_stems = _collect_base_stems(base_files, base_dirs)
    base_stems_cf = {s.casefold() for s in base_stems}
    base_imp_cf = {s.casefold() for s in base_imports}

    for fpath in added_py:
        for exp_dir, stem in _exposed_module_pairs(fpath, base_dirs):
            added_module_dirs.add(exp_dir)
            cf = stem.casefold()
            if (
                stem in _STDLIB_NAMES
                or cf in _STDLIB_NAMES_CF
                or stem in _PROTECTED_MODULES
                or cf in _PROTECTED_CF
                or stem in base_stems
                or cf in base_stems_cf
                or stem in base_imports
                or cf in base_imp_cf
            ):
                shadow_dirs.add(exp_dir)
    return frozenset(shadow_dirs), frozenset(added_module_dirs), cand_py_oids


def _parse_diff_z(raw: bytes) -> list[tuple[str, str]]:
    tokens = raw.split(b"\x00")
    changed_files: list[tuple[str, str]] = []
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
    return changed_files


def load_diff_info(
    repo: Path, base_sha: str, cand_sha: str
) -> tuple[list[tuple[str, str]] | None, frozenset[str]]:
    """Load changed files and accurate shadow-contaminated directories."""
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
    changed_files = _parse_diff_z(p.stdout)
    res = _compute_shadows(repo, base_sha, cand_sha, changed_files)
    if res is None:
        return None, frozenset()
    return changed_files, res[0]


def load_cand_shadow_extras(
    repo: Path, base_sha: str, cand_sha: str, changed_files: list[tuple[str, str]]
) -> tuple[frozenset[str], dict[str, str]] | None:
    """Load cross-dir added module dirs and candidate .py blob OIDs."""
    res = _compute_shadows(repo, base_sha, cand_sha, changed_files)
    if res is None:
        return None
    return res[1], res[2]
