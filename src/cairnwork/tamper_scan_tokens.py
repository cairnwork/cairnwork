"""Python's own reading of a changed file, line by line, for tamper_scan.py (#571).

tamper_scan reads added lines one at a time with regular expressions. Whether a ``#`` starts a
comment and whether a ``;`` ends a statement depends on the lines before it: a string literal
opened on an earlier line (an unchanged one included) holds both as plain text. This module runs
``tokenize`` over the whole file as committed and returns, for each physical line, a view of it:

* ``code``: the line with each ``#`` and ``;`` inside a string literal (or the literal part of
  an f-string) replaced by ``x``. A ``#`` left in it starts a comment and a ``;`` left in it
  separates statements, as Python reads them; the rest of the line is as written.
* ``skip``: the line holds no code for Python: only a comment, or text of a plain string
  literal (not an f-string or t-string, whose braces hold code), or both.

tamper_scan scans ``code`` in addition to the line as written, so the view only adds findings, and
skips a ``#`` line only when ``skip`` agrees. A file that is not UTF-8 or does not tokenize
gets no views, so none of its lines is skipped as a comment (fail closed). Lines are numbered as
git numbers them (split at ``"\\n"``); each git line is cut at ``"\\r"`` into pieces, as Python
also ends a line there, and a view is keyed by (git line number, piece index).
"""

from __future__ import annotations

import io
import tokenize
from dataclasses import dataclass

_MASK = "x"
_QUOTES = frozenset("'\"")
_SYNTAX = frozenset("#;")  # read as syntax outside a string, as text inside one
_PARTS = ("START", "MIDDLE", "END")
_PREFIX_CODE = frozenset("fFtT")  # an f-string or t-string holds code in its braces (3.11: STRING)
# Tokens that put no code on a row: string text and quotes, a comment, line ends, indentation.
# The code in an f-string's braces (3.12+) comes as its own tokens, which mark the row as code.
_NO_CODE = frozenset(
    {tokenize.STRING, tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE, tokenize.INDENT}
    | {tokenize.DEDENT, tokenize.ENDMARKER}
    | {
        getattr(tokenize, f"{kind}STRING_{part}")
        for kind in "FT"
        for part in _PARTS
        if hasattr(tokenize, f"{kind}STRING_{part}")
    }
)
# String literal text: STRING, and the literal parts of f-strings and t-strings (3.12+, 3.14+).
_TEXT_TOKENS = frozenset(
    getattr(tokenize, name)
    for name in ("STRING", "FSTRING_MIDDLE", "TSTRING_MIDDLE")
    if hasattr(tokenize, name)
)


@dataclass(frozen=True)
class LineView:
    code: str
    skip: bool


Views = dict[tuple[int, int], LineView]


def file_views(data: bytes) -> Views | None:
    """Views of every line of ``data`` (a whole ``.py`` file), or None when it cannot be read."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    keys, rows = _rows(text.removeprefix("\ufeff"))
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO("\n".join(rows)).readline))
    except (tokenize.TokenError, SyntaxError):
        return None
    if any(token.type == tokenize.ERRORTOKEN for token in tokens):
        return None  # Python 3.11 reports some unreadable source this way instead of raising
    chars = [list(row) for row in rows]
    comments: dict[int, int] = {}  # row index -> column where a real comment starts
    separators: set[tuple[int, int]] = set()
    code_rows: set[int] = set()  # rows holding a token other than plain string text or comment
    for token in tokens:
        if token.type == tokenize.COMMENT:
            comments[token.start[0] - 1] = token.start[1]
        elif token.type == tokenize.OP and token.string == ";":
            separators.add((token.start[0] - 1, token.start[1]))
        if token.type in _TEXT_TOKENS:
            _mask(chars, token.start, token.end)
        if token.type not in _NO_CODE or _PREFIX_CODE & set(_prefix(token)):
            code_rows.update(range(token.start[0] - 1, token.end[0]))
    views: Views = {}
    for index, (key, row) in enumerate(zip(keys, chars, strict=True)):
        start = comments.get(index, len(row))
        for column in range(start):  # outside a comment, a stray "#" or ";" is text, not syntax
            if row[column] == "#" or (row[column] == ";" and (index, column) not in separators):
                row[column] = _MASK
        views[key] = LineView("".join(row), index not in code_rows)
    return views


def _prefix(token: tokenize.TokenInfo) -> str:
    """A string literal's prefix (``rb``, ``f``); empty for any other token."""
    if token.type != tokenize.STRING:
        return ""
    return token.string[: min(token.string.find(q) for q in _QUOTES if q in token.string)]


def _rows(text: str) -> tuple[list[tuple[int, int]], list[str]]:
    """Python's lines of ``text`` and, for each, its (git line number, piece index)."""
    keys: list[tuple[int, int]] = []
    rows: list[str] = []
    for number, line in enumerate(text.split("\n"), start=1):
        for piece, part in enumerate(line.removesuffix("\r").split("\r")):  # "\r\n" ends one line
            keys.append((number, piece))
            rows.append(part)
    return keys, rows


def _mask(chars: list[list[str]], start: tuple[int, int], end: tuple[int, int]) -> None:
    """Replace each ``#`` and ``;`` of one string-literal token with ``_MASK``."""
    for row in range(start[0] - 1, end[0]):
        line = chars[row]
        first = start[1] if row == start[0] - 1 else 0
        last = end[1] if row == end[0] - 1 else len(line)
        for column in range(first, min(last, len(line))):
            if line[column] in _SYNTAX:
                line[column] = _MASK


def added_file(lines: list[tuple[int, str]]) -> bytes | None:
    """A stand-in file built from the added lines alone (blank where a line is not given)."""
    if not lines or min(number for number, _ in lines) < 1:
        return None
    rows = [""] * max(number for number, _ in lines)
    for number, text in lines:
        rows[number - 1] = text
    try:
        return "\n".join(rows).encode("utf-8")
    except UnicodeEncodeError:
        return None
