"""Verify that import path entries in a git tree are plain directories (not symlinks/submodules)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from cairnwork.gitcmd import GitCommandError, run_git
from cairnwork.pytest_config import PytestConfigError


class ImportPathError(PytestConfigError):
    code = "BAD_IMPORT_PATH"


def _normalize_paths(paths: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    """Strip only what the checkout path ignores too (a leading ``./``, a trailing ``/``);
    whitespace is part of the name the verifier's sys.path entry gets."""
    roots = []
    for r in paths:
        cleaned = r
        while cleaned.startswith("./"):
            cleaned = cleaned[2:]
        cleaned = cleaned.rstrip("/")
        if cleaned and cleaned != ".":
            roots.append(cleaned)
    return tuple(dict.fromkeys(roots))


def require_plain_dirs(repo: Path, commit: str, paths: tuple[str, ...] | list[str]) -> None:
    """Ensure import paths correspond to plain directory trees (mode 040000) in git.

    Every segment must be a plain name: an empty, ``.`` or ``..`` segment would make the walk
    stop at a name git never lists while the filesystem still resolves the rest, and a
    non-ASCII one can name a tree entry in another Unicode form on APFS. Children are listed
    from the tree object (``<commit>:<dir>``), so no pathspec magic applies to the name."""
    normalized = _normalize_paths(paths)
    for path in normalized:
        segments = path.split("/")
        bad = [s for s in segments if s in ("", ".", "..") or not s.isascii()]
        if bad:
            raise ImportPathError(f"Import path {path!r} has a segment that is not a plain name")
        current_dir = ""
        for segment in segments:
            try:
                out = run_git(repo, "ls-tree", "-z", f"{commit}:{current_dir}")
                matches = []
                for entry in out.split("\0"):
                    if not entry:
                        continue
                    meta, name = entry.split("\t", 1)
                    mode = meta.split(" ", 1)[0]
                    if name.casefold() == segment.casefold():
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
