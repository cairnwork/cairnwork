"""Shared typed refusals used by task and candidate boundaries."""

from cairnwork.textclean import is_hidden


def _shown_detail(detail: str) -> str:
    return "".join(
        f"\\x{ord(char):02x}"
        if is_hidden(char) and ord(char) <= 0xFF
        else f"\\u{ord(char):04x}"
        if is_hidden(char) and ord(char) <= 0xFFFF
        else f"\\U{ord(char):08x}"
        if is_hidden(char)
        else char
        for char in detail
    )


class TaskRejected(Exception):
    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        super().__init__(f"{code}: {_shown_detail(detail)}")


class CandidateRejected(Exception):
    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        super().__init__(f"{code}: {detail}")
