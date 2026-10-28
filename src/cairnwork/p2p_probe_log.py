"""Helper for reading the sanitized tail of a P2P base-probe log."""

from __future__ import annotations

import re
from pathlib import Path

_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")  # terminal colour and cursor codes, dropped whole
# other control characters, and the line and bidi overrides gitcmd.stderr_tail drops too
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f\u2028\u2029\u202a-\u202e\u2066-\u2069]")
_URL_USERINFO = re.compile(r"(?<=://)[^ \t\n]*@")  # credentials in a URL, as gitcmd.stderr_tail
# header values (any scheme), Bearer tokens and secret-named key=value fields (#366); the value runs
# to the end of the line, the next whitespace, or '&', so trailing punctuation is masked with it
_HEADER = re.compile(r"(?im)(\b(?:proxy-)?authorization:[ \t]*)[^\n]+")
_BEARER = re.compile(r"(?i)(\bbearer[ \t]+)\S+")
_KEYS = r"(?:access_|refresh_|id_|auth_)?token|password|passwd|pwd|secret|client_secret|api_?key"
_FIELD = re.compile(rf"(?i)((?<![\w-])(?:{_KEYS})=)[^&\s]+")
TAIL_CHARS = 2048
# any character fits in 4 bytes; twice that, so the window holds more than the tail. A window that
# does not start the file drops its first, partial line: a character or URL cut there never reaches
# the tail, however much sanitizing shrinks the rest (#335)
_WINDOW_BYTES = 2 * 4 * TAIL_CHARS


def probe_log_tail(path: str | Path | None) -> str:
    """Return the last TAIL_CHARS characters of ``path`` sanitized, with URL credentials removed
    and the forms above masked, or ``(no log)`` on error/missing.

    Not masked: credentials split by a raw control byte (it becomes '?'), a quoted value with a
    space in it, JSON fields, scp-style ``user:pass@host:`` and bare high-entropy strings.
    """
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
    if start:  # URL credentials never span a newline; with no newline, nothing is known safe
        raw = raw.partition(b"\n")[2]
        if not raw:
            return f"[tail omitted: no complete line in the last {_WINDOW_BYTES // 1024} KB]"
    text = raw.decode("utf-8", errors="replace")
    text = _URL_USERINFO.sub("", _CONTROL.sub("?", _ANSI.sub("", text).replace("\r\n", "\n")))
    text = _BEARER.sub(r"\1<redacted>", _HEADER.sub(r"\1<redacted>", text))
    return _FIELD.sub(r"\1<redacted>", text)[-TAIL_CHARS:]
