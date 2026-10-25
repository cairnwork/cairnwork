"""Parsing the worker's two fixed git queries for the local worker loop (#88, Plan v2 point 4).

``status`` is ``git status --porcelain=v2 --branch -z -uall --renames --ignored=matching`` (ignored
files are listed too, so a write to an ignored path still changes the observed tree), followed by
what git cannot see: ``x d <path>`` for an empty directory and ``x o <path>`` for a FIFO or socket
(Claude review of #239, NB10), so creating one changes the observed tree too; ``numstat``
is one line, ``PATCH <added> <removed> <binary_files_changed>``, counted from the ``--text -U0``
patch against HEAD with untracked and ignored files included. Output that does not parse is never
guessed into a tree or a count: it is reported as not observed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class TreeState:
    """One observed ``git status``: HEAD and the porcelain v2 entries (sorted), or not observed."""

    observed: bool
    head: str | None = None
    entries: tuple[str, ...] = ()
    # #261, with a start allow-list only: the digest query's records (parse_digest), kept in
    # memory for the start check, numstat and the cut's changed paths; the entries carry one
    # "r <sha256> <root>" record per allowed root instead (base_check.compress)
    digest: tuple[str, ...] = field(default=(), compare=False, repr=False)

    def paths(self) -> list[str]:
        return sorted({p for e in self.entries for p in _entry_paths(e)})


def _entry_paths(entry: str) -> list[str]:
    kind = entry[:1]
    if kind in ("?", "!"):
        return [entry[2:]]
    if kind == "1":
        return [entry.split(" ", 8)[-1]]
    if kind == "2":
        head, _, orig = entry.partition("\0")
        return [head.split(" ", 9)[-1], orig]
    if kind == "u":
        return [entry.split(" ", 10)[-1]]
    if kind == "x":
        return [entry[4:]]
    if kind == "h":  # a digest record: "h <mode> <blob> <status> <path>" (parse_digest)
        return [entry.split(" ", 4)[-1]]
    if kind == "r":  # an allowed root's digest hash: "r <sha256> <root>" (base_check.compress)
        return [entry.split(" ", 2)[-1]]
    return []


def parse_status(raw: bytes) -> TreeState:
    """Parse ``git status --porcelain=v2 --branch -z`` output (rename entries carry two paths)."""
    fields = raw.decode("utf-8", errors="surrogateescape").split("\0")
    head: str | None = None
    entries: list[str] = []
    i = 0
    while i < len(fields):
        f = fields[i]
        if f.startswith("# branch.oid "):
            oid = f.removeprefix("# branch.oid ")
            head = oid if re.fullmatch(r"[0-9a-f]{40,64}", oid) else None
        elif f[:2] in ("1 ", "u ", "? ", "! "):
            entries.append(f)
        elif f.startswith("2 ") and i + 1 < len(fields):
            entries.append(f + "\0" + fields[i + 1])
            i += 1
        elif f[:6] in ("x d ./", "x o ./") or f in ("x d .", "x o ."):
            entries.append(f[:4] + (f[6:] or "."))  # find's "./path" as the checkout path
        elif f and not f.startswith("# "):
            return TreeState(observed=False)  # not git porcelain: never guess a tree
        i += 1
    return TreeState(observed=True, head=head, entries=tuple(sorted(entries)))


_OID = r"(?:[0-9a-f]{40}|[0-9a-f]{64})"
_RAW = re.compile(rf":([0-7]{{6}}) ([0-7]{{6}}) ({_OID}) ({_OID}) ([ADMT])")


def parse_digest(raw: bytes) -> tuple[str, ...] | None:
    """The ``digest`` query (#261: ``git diff --cached --raw -z --no-abbrev HEAD`` of the tree
    staged with every untracked and ignored file) as sorted ``h <new mode> <new blob> <status>
    <path>`` records, one per path that differs from HEAD; None unless every record parses."""
    fields = raw.decode("utf-8", errors="surrogateescape").split("\0")
    if fields[-1] != "" or len(fields) % 2 == 0:
        return None  # not NUL-terminated (pairs of meta and path, then the final empty field)
    records = []
    for meta, path in zip(fields[0:-1:2], fields[1:-1:2], strict=True):
        m = _RAW.fullmatch(meta)
        if m is None or not path:
            return None
        records.append(f"h {m.group(2)} {m.group(4)} {m.group(5)} {path}")
    return tuple(sorted(records))


_PATCH = re.compile(rb"PATCH (\d{1,18}) (\d{1,18}) (\d{1,18})\n")


def parse_numstat(raw: bytes) -> tuple[int, int, int] | None:
    """(lines added, lines removed, binary_files_changed) from the wrapper's numstat line.

    Claude review of #239, NB2: ``git diff --numstat`` shows a file it takes for binary (one NUL
    byte is enough) as ``-`` lines, and ``--text`` does not change that, so summing numstat let
    one NUL byte hide a whole file's patch. The wrapper therefore counts added and removed lines
    from the hunk headers of ``git diff --cached --text -U0`` (every file's lines, binary or not)
    and also reports how many files numstat would have shown as binary, so a reader can see
    which counts would have been hidden. Anything but exactly that one line is None."""
    m = _PATCH.fullmatch(raw)
    if m is None:
        return None
    added, removed, binary = (int(g) for g in m.groups())
    return added, removed, binary


def parse_tracked(raw: bytes) -> frozenset[str] | None:
    """The base's paths from the worker's ``tracked`` query (``git ls-tree -r -t -z --name-only
    HEAD``: every file and directory of HEAD), plus "." for the checkout root; None unless every
    record is non-empty and NUL-terminated."""
    if raw and not raw.endswith(b"\0"):
        return None
    names = raw.decode("utf-8", errors="surrogateescape").split("\0")[:-1] if raw else []
    if not all(names):
        return None
    return frozenset([".", *names])
