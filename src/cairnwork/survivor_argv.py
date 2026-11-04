"""Which processes outlived a run (#814): the start of each one's argv, read before its kill.

Only a diagnostic for the task author; the verdict still follows the count alone. An argument may
hold a secret, so each line goes through the P2P log's masking (p2p_probe_log.mask_secrets) before
it is cut to ARGV_ITEMS arguments and ARGV_CHARS characters. The environment is never read.
"""

from __future__ import annotations

import re
import subprocess
import sys

from cairnwork.p2p_probe_log import mask_secrets

ARGV_ITEMS, ARGV_CHARS, MAX_LISTED = 3, 80, 5
RAW_CHARS = 4096  # read this much of an argv, no more: the masking cost grows with its input
# the record delimiters of reasons.explain(), as reasons._names (no import: reasons imports this)
_DELIMITERS = re.compile(r"[\[\];:]")
HIDDEN = "<redacted>"
_VALUE_FLAGS = frozenset({"-u", "-a", "-p", "--user", "--auth"})  # their next word may be a secret
UNREAD = "(argv not read)"


class Survivors(int):
    """The survivor count of a run, with the argv prefixes seen. An int in every other respect
    (compared, summed, stored, written as JSON): the verdict never reads ``argv``."""

    argv: tuple[str, ...]

    def __new__(cls, count: int, argv: tuple[str, ...] = ()) -> Survivors:
        self = super().__new__(cls, count)
        self.argv = tuple(argv)
        return self

    def __add__(self, other: int) -> Survivors:
        return Survivors(int(self) + other, self.argv)


def _hide_values(words: list[str]) -> list[str]:
    """Words after a masked value, or after -u/-a/-p, up to the next flag are hidden too: a quoted
    value with a space in it, or ``user:password``, is more than one word once ps has joined it."""
    out, hiding = [], False
    for word in words:
        hiding = hiding and not word.startswith("-")
        out.append(HIDDEN if hiding and word != HIDDEN else word)
        hiding = hiding or word == HIDDEN or word in _VALUE_FLAGS
    return [w for i, w in enumerate(out) if not (i and w == HIDDEN == out[i - 1])]


def prefix(text: str) -> str:
    """The cut (RAW_CHARS), masked, then shortened form of one argv text; never raises. The order
    matters: cutting last keeps a secret from being split, cutting first bounds the masking."""
    words = _hide_values(mask_secrets(text[:RAW_CHARS]).split())[:ARGV_ITEMS]
    shown = _DELIMITERS.sub("?", " ".join(words))
    return shown[:ARGV_CHARS] + ("..." if len(shown) > ARGV_CHARS else "") or UNREAD


def read_argv(pid: int) -> str:
    """``pid``'s argv as one line, or ``""`` when it cannot be read (exited, hidden, no ps)."""
    try:
        if sys.platform.startswith("linux"):
            with open(f"/proc/{pid}/cmdline", "rb") as handle:
                return handle.read(RAW_CHARS).decode("utf-8", "replace").replace("\0", " ")
        done = subprocess.run(  # noqa: S603 - fixed argv, pid is an integer; no -E: no environment
            ["/bin/ps", "-ww", "-o", "args=", "-p", str(pid)],
            capture_output=True, timeout=5, check=False,
        )  # fmt: skip
        return done.stdout[:RAW_CHARS].decode("utf-8", "replace") if done.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def note(seen: dict[int, str], pid: int) -> None:
    """Record ``pid``'s argv prefix in ``seen`` (at most MAX_LISTED pids, each once)."""
    if pid not in seen and len(seen) < MAX_LISTED:
        seen[pid] = prefix(read_argv(pid))


def listing(survivors: object) -> str:
    """``" (argv: a b, c d)"`` for a Survivors that saw some, else ``""``."""
    argv = getattr(survivors, "argv", ())
    return f" (argv: {', '.join(argv)})" if argv else ""
