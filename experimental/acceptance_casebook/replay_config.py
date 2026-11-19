"""Redacted state-config provenance for replay records."""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path
from typing import Any


def metadata(path: Path | None, runner: Any = None) -> dict[str, str | None]:
    """Return presence and byte digest without exposing any config values."""
    if path is None:
        return {"state_config": "none", "state_config_sha256": None}
    copied_digest = getattr(runner, "state_config_sha256", None)
    if copied_digest is None:
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, "rb") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    return {"state_config": "given", "state_config_sha256": None}
                digest = hashlib.sha256()
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
                copied_digest = digest.hexdigest()
        except OSError:
            copied_digest = None
    return {"state_config": "given", "state_config_sha256": copied_digest}
