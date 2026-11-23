"""One definition of "invisible" for the secret scan (#1204, #1556): the plain text and every
decoded stretch are cleaned by it, so a change here applies to both."""

from __future__ import annotations

import re
import unicodedata

_ASCII_CONTROL = re.compile(r"[\x00-\x08\x0e-\x1f\x7f]")
_FILLERS = frozenset("\u115f\u1160\u3164\uffa0\u2800")  # Default_Ignorable, not in C*/Mn/Me


def drop_invisible(text: str, repl: str = "") -> str:
    """``text`` without invisible characters, chosen by Unicode category as textclean does: C*
    (control, format, private use, unassigned) except whitespace, the combining marks Mn/Me, and
    the fillers outside those categories (Hangul fillers, Braille blank). ``repl`` stands in for
    each one (a space: an invisible character may itself be the separator)."""

    if text.isascii() and not _ASCII_CONTROL.search(text):
        return text  # nothing to drop; skips a per-character pass over a large input

    def invisible(ch: str) -> bool:
        cat = unicodedata.category(ch)
        return (cat[0] == "C" and not ch.isspace()) or cat in ("Mn", "Me") or ch in _FILLERS

    return "".join(repl if invisible(ch) else ch for ch in text)
