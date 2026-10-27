"""Verify that import path entries in a git tree are plain directories (not symlinks/submodules)."""

from __future__ import annotations

import subprocess
import unicodedata
from pathlib import Path

from cairnwork.gitcmd import GitCommandError, run_git
from cairnwork.pytest_config import PytestConfigError


class ImportPathError(PytestConfigError):
    code = "BAD_IMPORT_PATH"


def normalize_import_paths(paths: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    """The one import-path normalizer (#300): this module's tree walk and verifier_spec's shadow
    gate both use it, so they always reason about the same directories.

    Strip only what the checkout path ignores too (a leading ``./``, a trailing ``/``);
    whitespace is part of the name the verifier's sys.path entry gets. Every segment must be a
    plain name: an empty, ``.`` or ``..`` segment would make the walk stop at a name git never
    lists while the filesystem still resolves the rest, and a non-ASCII one can name a tree
    entry in another Unicode form on APFS. Such an entry raises ImportPathError."""
    roots = []
    for r in paths:
        cleaned = r
        while cleaned.startswith("./"):
            cleaned = cleaned[2:]
        cleaned = cleaned.rstrip("/")
        if not cleaned or cleaned == ".":
            continue
        if any(s in ("", ".", "..") or not s.isascii() for s in cleaned.split("/")):
            raise ImportPathError(f"Import path {cleaned!r} has a segment that is not a plain name")
        roots.append(cleaned)
    return tuple(dict.fromkeys(roots))


def _name_key(name: str) -> str:
    """APFS compares names after Unicode decomposition: U+037E opens as ``;``."""
    return unicodedata.normalize("NFD", unicodedata.normalize("NFD", name).casefold())


def require_plain_dirs(repo: Path, commit: str, paths: tuple[str, ...] | list[str]) -> None:
    """Ensure import paths correspond to plain directory trees (mode 040000) in git.

    Segments are checked by ``normalize_import_paths``. Children are listed from the tree
    object (``<commit>:<dir>``), so no pathspec magic applies to the name."""
    for path in normalize_import_paths(paths):
        current_dir = ""
        for segment in path.split("/"):
            try:
                out = run_git(repo, "ls-tree", "-z", f"{commit}:{current_dir}")
                matches = []
                for entry in out.split("\0"):
                    if not entry:
                        continue
                    meta, name = entry.split("\t", 1)
                    mode = meta.split(" ", 1)[0]
                    if _name_key(name) == _name_key(segment):
                        matches.append((mode, name))
            except (GitCommandError, OSError, ValueError, subprocess.SubprocessError) as exc:
                raise ImportPathError(f"Failed to inspect import path {path!r}: {exc}") from exc

            if len(matches) == 0:
                break
            if len(matches) > 1:
                msg = f"Import path {path!r} segment {segment!r} is ambiguous in tree"
                raise ImportPathError(msg)
            mode, name = matches[0]
            if mode != "040000":
                raise ImportPathError(
                    f"Import path {path!r} segment {segment!r} is not a directory (mode {mode})"
                )
            current_dir = f"{current_dir}{name}/"
