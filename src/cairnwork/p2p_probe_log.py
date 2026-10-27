"""Helper for reading the sanitized tail of a P2P base-probe log."""

from __future__ import annotations

import re
from pathlib import Path

_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def probe_log_tail(path: str | Path | None) -> str:
    """Return the last 2048 bytes of ``path`` sanitized, or ``(no log)`` on error/missing."""
    if not path:
        return "(no log)"
    try:
        p = Path(path)
        with p.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 2048))
            raw = f.read(2048)
    except OSError:
        return "(no log)"
    text = raw.decode("utf-8", errors="replace")
    return _CONTROL.sub("?", text)
