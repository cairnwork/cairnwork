"""The output and file-size caps of a confined run (moved from confined.py, which re-exports
them): the parent copies the child's output up to the cap, and the child's files are capped."""

from __future__ import annotations

import os
import resource

MAX_LOG_BYTES = 4 << 20
MAX_REPORT_BYTES = 4 << 20


def copy_capped(source, target) -> None:
    """Copy at most MAX_LOG_BYTES + 1 bytes (one over the cap marks the log too big)."""
    written = 0
    for chunk in iter(lambda: source.read(1 << 16), b""):
        keep = chunk[: max(0, MAX_LOG_BYTES + 1 - written)]
        if keep:
            target.write(keep)
            written += len(keep)


def _limit_file_size() -> None:
    """In the child: no file it writes (log, report, scratch) may exceed the caps, and
    the files it creates are group-readable whatever the caller's umask (the report
    of a verifier that runs as another account must be readable by the owner)."""
    os.umask(0o022)
    limit = MAX_LOG_BYTES + MAX_REPORT_BYTES
    resource.setrlimit(resource.RLIMIT_FSIZE, (limit, limit))
