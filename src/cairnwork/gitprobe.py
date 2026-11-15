"""Environment probes that tell git infrastructure failures from candidate failures (#1298)."""

from __future__ import annotations

import errno
import os
import shutil
import tempfile
from pathlib import Path

from cairnwork.gitcmd import NETWORK_TIMEOUT_SECONDS, GitCommandError, run_git


def storage_exhausted(*dirs: Path) -> bool:
    """True when a directory cannot take a small write (ENOSPC, EDQUOT, EROFS) or has under
    1 MiB free. Probes the filesystem; never reads git's text, which a candidate can shape."""
    for where in dirs:
        try:
            if shutil.disk_usage(where).free < 1 << 20:
                return True
            with tempfile.TemporaryFile(dir=where) as probe:
                probe.write(b"\0" * 4096)
                probe.flush()
                os.fsync(probe.fileno())
        except OSError as exc:
            if exc.errno in (errno.ENOSPC, errno.EDQUOT, errno.EROFS):
                return True
    return False


def origin_reachable(repo: Path) -> None:
    """Raise OSError unless origin exists and answers: a missing remote, network or permission
    failure is infrastructure, not a verdict on the candidate (checked before any fetch)."""
    try:
        run_git(repo, "ls-remote", "--quiet", "origin", "HEAD", timeout=NETWORK_TIMEOUT_SECONDS)
    except GitCommandError as exc:
        raise OSError(f"Cairnwork could not reach origin: {exc}") from exc
