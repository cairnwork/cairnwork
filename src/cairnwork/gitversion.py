"""Feature detection for Git's global no-lazy-fetch option (#489)."""

from __future__ import annotations

import functools
import os
import shutil
import subprocess


class GitVersionError(Exception):
    """Git is missing or does not support the global ``--no-lazy-fetch`` option."""


_TIMEOUT = 10


@functools.cache
def git_executable() -> str:
    """Resolve and cache the absolute Git executable used by probes and commands."""
    found = shutil.which("git")
    if found is None:
        raise GitVersionError("git executable not found; requires Git supporting --no-lazy-fetch")
    return os.path.abspath(found)  # absolute, symlinks kept (a multi-call binary keys on its name)


@functools.cache
def require_no_lazy_fetch() -> None:
    """Fail closed unless this Git accepts ``--no-lazy-fetch`` as a global option."""
    executable = git_executable()
    try:
        result = subprocess.run(
            [executable, "--no-lazy-fetch", "version"],
            capture_output=True,
            check=False,
            timeout=_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise GitVersionError(
            "could not verify Git support for global --no-lazy-fetch; refusing to run git"
        ) from exc
    if result.returncode != 0:
        raise GitVersionError("requires Git supporting global --no-lazy-fetch; refusing to run git")
