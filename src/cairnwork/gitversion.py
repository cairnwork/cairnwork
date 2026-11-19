"""Minimum git version for the no-lazy-fetch guard (#489)."""

from __future__ import annotations

import functools
import re
import subprocess


class GitVersionError(Exception):
    """git is missing, unparsable or older than MIN_GIT."""


MIN_GIT = (2, 45, 0)  # GIT_NO_LAZY_FETCH needs git >= 2.45; older git ignores it silently
_TIMEOUT = 10


def parse_git_version(output: str) -> tuple[int, int, int] | None:
    match = re.search(r"\bgit version (\d+)\.(\d+)\.(\d+)", output)
    return tuple(map(int, match.groups())) if match else None


@functools.cache
def git_version() -> tuple[int, int, int]:
    try:
        output = subprocess.run(
            ["git", "--version"],
            capture_output=True,
            text=True,
            check=True,
            timeout=_TIMEOUT,
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise GitVersionError("could not determine git version; refusing to run git") from exc
    version = parse_git_version(output)
    if version is None:
        raise GitVersionError("could not parse git version; refusing to run git")
    if version < MIN_GIT:
        raise GitVersionError(
            f"requires git >= 2.45 for GIT_NO_LAZY_FETCH; found {'.'.join(map(str, version))}; "
            "refusing to run git"
        )
    return version
