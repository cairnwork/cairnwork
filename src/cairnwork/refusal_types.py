"""Shared typed refusals used by task and candidate boundaries."""

import unicodedata

from cairnwork.textclean import is_hidden

_FILLERS = frozenset("\u115f\u1160\u3164\uffa0\u2800")  # invisible Lo/So letters


def _invisible(char: str) -> bool:
    """Hidden by category (C*, Zl, Zp), a combining mark (Mn, Me: U+034F, selectors) or a filler."""
    return is_hidden(char) or unicodedata.category(char) in ("Mn", "Me") or char in _FILLERS


def _shown_detail(detail: str) -> str:
    return "".join(
        f"\\x{ord(char):02x}"
        if _invisible(char) and ord(char) <= 0xFF
        else f"\\u{ord(char):04x}"
        if _invisible(char) and ord(char) <= 0xFFFF
        else f"\\U{ord(char):08x}"
        if _invisible(char)
        else char
        for char in detail
    )


class TaskRejected(Exception):
    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        super().__init__(f"{code}: {_shown_detail(detail)}")


class CandidateRejected(Exception):
    def __init__(self, code: str, detail: str) -> None:
        self.code = code  # escaped here too, so a call site that forgets ``shown`` stays safe
        super().__init__(f"{code}: {_shown_detail(detail)}")
