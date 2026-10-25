"""Harness-declared untracked or ignored paths the start check allows (#261), off unless set.

Some evaluation harnesses lay generated files into the checkout before the worker starts (copies
from a worktree, stub directories, generated fixtures), usually git-ignored through
``.git/info/exclude``. Without an allow-list any status entry refuses the start
(agent.RefusedStart). With one, an untracked (``?``) or ignored (``!``) entry, or an empty
directory (``x d``), passes when its path is allowed:

* ``--allow-untracked PATH``: a canonical repo-relative path without glob characters; it allows
  that path and everything under it, by whole path segments ("data" allows "data" and "data/x",
  never "data2/x").
* ``--allow-untracked-glob GLOB``: an fnmatch pattern (case-sensitive; ``*`` also matches "/")
  that allows a path when the path or one of its leading segment prefixes matches it. It needs
  at least one character besides ``*``, ``?`` and ``/``.

Everything else still refuses: a changed, staged, deleted or renamed tracked file (allowed path
or not), an untracked or ignored file outside the list, a FIFO or socket anywhere, a HEAD other
than the pinned base, any symlink or nested repository among the untracked or ignored files, and
an untracked ``.gitignore`` (the base's ignore rules come from HEAD and ``.git/info/exclude``).
A symlink is refused wherever it points: neither the status nor the digest query can say where
its target is (inside the checkout, outside it, or a path that will change), so a test reading
through it could read files the observed tree does not cover, and refusing it is the only choice
the loop can check.

With a list set, every observation also runs the wrapper's ``digest`` query (one call, whatever
the number of files: the mode and blob id of every path that differs from HEAD, ignored files
included), because the status alone shows an ignored directory as one entry and an untracked
file without its content: a write into an allowed path would otherwise not change the observed
tree. The tree records one ``r <sha256> <root>`` entry per allowed root (each allowed status
entry of the base) over the digest records under it, so the recorded tree stays small. The cut
thus falls at the first turn after which any allowed file's content, mode or presence differs
from base, as it does for every other path. numstat subtracts the base's own count (the allowed
files' lines), so it counts the worker's edits only; when a file present at base under an
allowed root changed, the two cannot be told apart and the counts are null (cleanup "failed").
Without a list the digest query is never called, so the wrapper from before it still serves.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Iterable
from dataclasses import dataclass

from experimental.local_worker.cuts import is_ignore_file
from experimental.local_worker.gitstate import TreeState
from experimental.local_worker.write_scope import canonical_entry

UNTRACKED_ALLOW_RULE = (  # recorded in manifest.json when a corpus runs with an allow-list
    "untracked_allow: at start, an untracked or ignored status entry or an empty directory "
    "passes when its path equals a 'paths' entry or lies under one (whole path segments), or "
    "when the path or one of its leading segment prefixes fnmatches a 'globs' entry "
    "(case-sensitive); tracked changes, other entries, FIFOs, sockets, symlinks, nested "
    "repositories and untracked .gitignore files still refuse. Each observation then also "
    "runs the digest query (mode and blob id of every path that differs from HEAD, ignored "
    "files included) and the tree carries one sha256 per allowed root over the digest records"
    " under it, so first_edit_cut and first_source_edit see a write to an allowed path; "
    "changed_paths names the files under a changed root, and a root or file that is "
    "git-ignored is no source edit. numstat is the final count less the base's; it is null "
    "(cleanup failed) when a file present at base under an allowed root changed. A resume's "
    "base must have the parent's base entries exactly."
)
_GLOB_CHARS = ("*", "?", "[")
_ALLOWED_MODES = ("100644", "100755")  # regular files; 120000 (symlink), 160000 (repo) refuse


def _entry(raw: str, glob: bool) -> str:
    flag = "--allow-untracked-glob" if glob else "--allow-untracked"
    try:  # relative, no "", ".", ".." or .git part, no NUL, newline, quote or backslash
        entry = canonical_entry(raw)
    except ValueError:
        raise ValueError(f"{flag} {raw!r} is not a canonical repo-relative path") from None
    has_glob = any(c in entry for c in _GLOB_CHARS)
    if not glob and has_glob:
        raise ValueError(f"{flag} {raw!r} has a glob character: use --allow-untracked-glob")
    if glob and not entry.strip("*?/"):
        raise ValueError(f"{flag} {raw!r} matches any path")
    return entry


@dataclass(frozen=True)
class UntrackedAllow:
    paths: tuple[str, ...] = ()
    globs: tuple[str, ...] = ()
    expected: tuple[str, ...] | None = None  # a resume: the parent's base entries, exactly

    @property
    def active(self) -> bool:
        return bool(self.paths or self.globs)

    def manifest(self) -> dict:
        return {"paths": list(self.paths), "globs": list(self.globs), "rule": UNTRACKED_ALLOW_RULE}

    def matches(self, path: str) -> bool:
        p = path.rstrip("/")  # git lists an ignored directory as "dir/"
        if any(p == e or p.startswith(e + "/") for e in self.paths):
            return True
        parts = p.split("/")
        prefixes = ["/".join(parts[:i]) for i in range(1, len(parts) + 1)]
        return any(fnmatch.fnmatchcase(x, g) for g in self.globs for x in prefixes)

    def refusal(self, base: TreeState) -> str | None:
        """Why ``base`` (its status entries and its full digest records) is not a clean start
        under this list, or None."""
        for e in base.entries + base.digest:
            path = base_path(e)
            allowed = self.matches(path) and not is_ignore_file(path)  # rules: HEAD's, exclude's
            if e.startswith(("? ", "! ", "x d ")) and allowed:
                continue
            if e.startswith("h ") and self._allowed_record(e):
                continue
            kind = e[:3] if e.startswith("x ") else e[:1]
            return f"entry not allowed: {kind} {path!r}"
        return None

    def _allowed_record(self, record: str) -> bool:
        """A new regular file under an allowed path. An ignore file here is no rule git reads:
        it lies in an ignored directory (git never descends into one) or also has a status
        entry, refused above."""
        _, mode, _, status, path = record.split(" ", 4)
        return status == "A" and mode in _ALLOWED_MODES and self.matches(path)


def roots(base: TreeState) -> tuple[str, ...]:
    """The allowed roots: the paths of the base's untracked, ignored and empty-directory entries
    (an ignored directory as "dir/"), each hashed as one record from then on."""
    return tuple(sorted(base_path(e) for e in base.entries if e.startswith(("? ", "! ", "x d "))))


def under(path: str, root: str) -> bool:
    r = root.rstrip("/")
    return path == r or path.startswith(r + "/")


def base_path(entry: str) -> str:
    return TreeState(observed=True, entries=(entry,)).paths()[0]


def build(paths: Iterable[str] | None, globs: Iterable[str] | None) -> UntrackedAllow:
    """The list from the CLI flags, as sorted unique canonical entries (else ValueError)."""
    return UntrackedAllow(
        tuple(sorted({_entry(p, glob=False) for p in paths or ()})),
        tuple(sorted({_entry(g, glob=True) for g in globs or ()})),
    )
