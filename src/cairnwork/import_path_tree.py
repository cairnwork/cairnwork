"""Verify that import path entries in a git tree are plain directories (not symlinks/submodules)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from cairnwork.gitcmd import GitCommandError, run_git
from cairnwork.pytest_config import PytestConfigError


class ImportPathError(PytestConfigError):
    code = "BAD_IMPORT_PATH"


def _normalize_paths(paths: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    roots = []
    for r in paths:
        cleaned = r.strip()
        while cleaned.startswith("./"):
            cleaned = cleaned[2:]
        cleaned = cleaned.strip("/")
        if cleaned and cleaned != ".":
            roots.append(cleaned)
    return tuple(dict.fromkeys(roots))


def require_plain_dirs(repo: Path, commit: str, paths: tuple[str, ...] | list[str]) -> None:
    """Ensure import paths correspond to plain directory trees (mode 040000) in git."""
    normalized = _normalize_paths(paths)
    for path in normalized:
        segments = path.split("/")
        current_dir = ""
        for segment in segments:
            try:
                if not current_dir:
                    out = run_git(repo, "ls-tree", "-z", commit)
                else:
                    out = run_git(repo, "ls-tree", "-z", commit, "--", f"{current_dir}/")
                matches = []
                for entry in out.split("\0"):
                    if not entry:
                        continue
                    meta, name = entry.split("\t", 1)
                    mode = meta.split(" ", 1)[0]
                    basename = name.rsplit("/", 1)[-1]
                    if basename.casefold() == segment.casefold():
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
            current_dir = name
