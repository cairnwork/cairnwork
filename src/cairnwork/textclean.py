"""One category-based cleaner for text that goes into a reason, a log tail or a refusal (#740).

A hand-listed range misses zero-width and direction characters (U+200B, U+061C, U+FEFF ...), so
the test is the Unicode category: C* (control, format, surrogate, private use, unassigned) and
the line and paragraph separators Zl, Zp. Same rule as experimental/blind_warn (#726).
"""

from __future__ import annotations

import unicodedata
from collections.abc import Callable


def is_hidden(ch: str) -> bool:
    """True for a character of category C* or Zl or Zp."""
    cat = unicodedata.category(ch)
    return cat[0] == "C" or cat in ("Zl", "Zp")


def replace_hidden(text: str, repl: str | Callable[[str], str] = "?", keep: str = "") -> str:
    """``text`` with each hidden character replaced by ``repl`` (a string, or a function of the
    character); the characters in ``keep`` stay."""
    return "".join(
        (repl(c) if callable(repl) else repl) if is_hidden(c) and c not in keep else c for c in text
    )
