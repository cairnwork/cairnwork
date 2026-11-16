"""Owner-side single-use declaration commitment store."""

from __future__ import annotations

import os
import re
import time
from pathlib import Path

_COMMITMENT = re.compile(r"[0-9a-f]{64}\Z")


class OnceStore:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)

    def _path(self, commitment: str) -> Path:
        if not _COMMITMENT.fullmatch(commitment):
            raise ValueError("commitment must be 64 lowercase hex digits")
        return self.root / f"{commitment}.used"

    def consume(self, commitment: str, candidate: str) -> bool:
        path = self._path(commitment)
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            return False
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(f"{candidate}\n{time.time_ns()}\n")
        return True

    def used(self, commitment: str) -> bool:
        return self._path(commitment).exists()
