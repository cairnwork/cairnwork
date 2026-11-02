"""Reading ``git diff -U0`` text into added lines for tamper_scan.py (moved from there as is)."""

from __future__ import annotations

import codecs
import re

from cairnwork.gitcmd import GitCommandError

# Characters in one added line that tamper_scan.py reads. A longer line is refused as such
# (``long_line``) and not read further: several of its patterns take time that grows with the
# square of a line's length, or with the number of aliases a line binds times its length (#642).
MAX_LINE = 4096
_HUNK = re.compile(r"@@ -\d+(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_INDEX = re.compile(r"index [0-9a-f]+\.\.([0-9a-f]+)")
# A new side with no lines to show: a deleted file (all zeros) or the empty blob (SHA-1, SHA-256).
_NO_LINES = re.compile(
    r"0+|e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"
    r"|473a0f4c3be8a93681a267e3b1e9a7dcda1185436fe141f7749120a303721813"
)


def added_lines(diff: str) -> dict[str, list[tuple[int, str]]]:
    """The added lines of a ``git diff -U0`` text, by new path with their new line numbers.

    A line ends only at "\\n" (git ends every diff line there; any other line break inside is
    content). Inside a hunk, its header's counts say how many old and new lines follow, so an
    added line that starts with "++ " or "@@" is content, not a file or hunk header. A diff whose
    lines do not match those counts raises GitCommandError (unreadable, so the change is refused),
    as does a file whose ``index`` line names new content but whose lines the diff does not show.
    """
    files: dict[str, list[tuple[int, str]]] = {}
    current, number, old_left, new_left = None, 0, 0, 0
    owed = False  # an "index" line named new content; its "+++" header has not come yet
    for raw in diff.removesuffix("\n").split("\n"):
        if old_left > 0 or new_left > 0:
            # "" is a blank context line (diff.suppressBlankEmpty); "\ No newline" counts for none
            kind = raw[:1] or " "
            if kind not in "+- \\":
                raise GitCommandError("diff line inside a hunk has no +, -, space or \\ prefix")
            if kind == "+" and current is not None:
                files[current].append((number, raw[1:]))
            old_left -= kind in "- "  # a bool counts as 0 or 1
            new_left -= kind in "+ "
            number += kind in "+ "
            if old_left < 0 or new_left < 0:
                raise GitCommandError("diff hunk has more lines than its header counts")
        elif raw.startswith(("+", "-", " ")) and not raw.startswith(("+++ ", "--- ")):
            raise GitCommandError("diff line outside a hunk")
        elif raw.startswith("diff --git ") and owed:
            raise GitCommandError("diff shows a changed file without its lines")
        elif index := _INDEX.match(raw):
            owed = not _NO_LINES.fullmatch(index.group(1))
        elif raw.startswith("+++ "):
            owed = False
            target = _unquote(raw[4:].rstrip("\t"))  # git appends a tab to a path with a space
            current = target[2:] if target.startswith("b/") else None
            if current is not None:
                files.setdefault(current, [])
        elif hunk := _HUNK.match(raw):
            old_left, number = int(hunk.group(1) or 1), int(hunk.group(2))
            new_left = int(hunk.group(3) or 1)
    if old_left > 0 or new_left > 0 or owed:
        raise GitCommandError("diff ends inside a hunk or without a changed file's lines")
    return files


def _unquote(name: str) -> str:
    """git C-quotes a path with special characters (``"b/a\\tb.py"``, octal escapes for bytes)."""
    if len(name) < 2 or not (name.startswith('"') and name.endswith('"')):
        return name
    raw = name[1:-1].encode("utf-8", "surrogateescape")
    return codecs.escape_decode(raw)[0].decode("utf-8", "surrogateescape")
