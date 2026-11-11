"""Which processes outlived a run (#814): the start of each one's argv, read before its kill.

Only a diagnostic for the task author; the verdict follows the count alone. An argument may hold a
secret in a spelling no masking rule names, so nothing is shown unless it is on an allow-list
(#864): a known runner's base name (PROGRAMS), a known harmless flag (FLAGS), and the module after
``-m`` when it is in MODULES. Every other word becomes ``<redacted>``; a runner not on the list
hides the whole line. If the filter itself raises, the line is hidden whole (ARGV_HIDDEN), never
shown through an older rule. The environment is never read.
"""

from __future__ import annotations

import re
import subprocess
import sys

ARGV_ITEMS, ARGV_CHARS, MAX_LISTED = 3, 80, 5
RAW_CHARS = 4096  # read this much of an argv, no more: the masking cost grows with its input
# the record delimiters of reasons.explain(), as reasons._names (no import: reasons imports this)
_DELIMITERS = re.compile(r"[\[\];:]")
HIDDEN = "<redacted>"
PROGRAMS = frozenset({
    "python", "pytest", "node", "git", "sh", "bash", "zsh", "npm", "npx", "uv", "ruby", "java",
    "go", "cargo", "make", "sleep", "gunicorn", "uvicorn",
})  # fmt: skip
_VERSIONED = re.compile(r"(python|ruby)([23](\.[0-9]{1,2})?)?")  # python3.12, not python3333...
FLAGS = frozenset({
    "-m", "-c", "-v", "-vv", "-q", "-x", "-s", "-B", "-I", "-E", "-V", "-h",
    "--version", "--help", "--serve",
})  # fmt: skip
MODULES = frozenset({"pytest", "unittest", "pip", "venv", "uvicorn", "gunicorn"})
ARGV_HIDDEN = "(argv hidden)"
UNREAD = "(argv not read)"


class Survivors(int):
    """The survivor count of a run, with the argv prefixes seen. An int in every other respect
    (compared, summed, stored, written as JSON): the verdict never reads ``argv``."""

    argv: tuple[str, ...]
    reasons: tuple[str, ...]

    def __new__(
        cls, count: int, argv: tuple[str, ...] = (), reasons: tuple[str, ...] = ()
    ) -> Survivors:
        self = super().__new__(cls, count)
        self.argv = tuple(argv)
        self.reasons = tuple(reasons)
        return self

    def __add__(self, other: int) -> Survivors:
        return Survivors(int(self) + other, self.argv, self.reasons)


def _allowed(words: list[str]) -> list[str]:
    """The allow-listed words of ``words``; each run of the others is one HIDDEN."""
    if not words:
        return []
    program = words[0].rsplit("/", 1)[-1].lower()
    if program not in PROGRAMS and not _VERSIONED.fullmatch(program):
        return [HIDDEN]
    out = [program]
    for before, word in zip(words, words[1:], strict=False):
        keep = word in FLAGS or (before == "-m" and word in MODULES)
        if keep or out[-1] != HIDDEN:
            out.append(word if keep else HIDDEN)
    return out


def prefix(text: str) -> str:
    """The cut (RAW_CHARS), allow-listed, then shortened form of one argv text; never raises, and a
    failure of the filter hides the whole line rather than falling back to another rule."""
    try:
        # The allow-list is primary; this defence-in-depth substitution is redundant.
        shown = _DELIMITERS.sub("?", " ".join(_allowed(text[:RAW_CHARS].split())[:ARGV_ITEMS]))
    except Exception:  # noqa: BLE001 - the diagnostic must not change the verdict
        return ARGV_HIDDEN
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
    """Format the bounded argv and fixed reason evidence for surviving work."""
    argv = getattr(survivors, "argv", ())
    reasons = getattr(survivors, "reasons", ())
    details = []
    if argv:
        details.append(f"argv: {', '.join(argv)}")
    if reasons:
        details.append(f"reason: {', '.join(reasons)}")
    return f" ({'; '.join(details)})" if details else ""
