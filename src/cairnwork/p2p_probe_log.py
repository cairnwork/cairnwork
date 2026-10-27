"""Helper for reading the sanitized tail of a P2P base-probe log."""

from __future__ import annotations

import re
from pathlib import Path

_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")  # terminal colour and cursor codes, dropped whole
# other control characters, and the line and bidi overrides gitcmd.stderr_tail drops too
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f\u2028\u2029\u202a-\u202e\u2066-\u2069]")
_URL_USERINFO = re.compile(r"(?<=://)[^ \t\n]*@")  # credentials in a URL, as gitcmd.stderr_tail
TAIL_CHARS = 2048
# any character fits in 4 bytes; twice that, so the window always holds more than the tail: a
# character or URL cut at the window's start falls outside the tail, and is redacted first
_WINDOW_BYTES = 2 * 4 * TAIL_CHARS


def probe_log_tail(path: str | Path | None) -> str:
    """Return the last TAIL_CHARS characters of ``path`` sanitized and with URL credentials
    removed, or ``(no log)`` on error/missing."""
    if not path:
        return "(no log)"
    try:
        p = Path(path)
        with p.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            start = max(0, size - _WINDOW_BYTES)
            f.seek(start)
            raw = f.read(_WINDOW_BYTES)
    except OSError:
        return "(no log)"
    text = raw.decode("utf-8", errors="replace")
    text = _CONTROL.sub("?", _ANSI.sub("", text).replace("\r\n", "\n"))
    return _URL_USERINFO.sub("", text)[-TAIL_CHARS:]
