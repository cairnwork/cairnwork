"""Masks for the outside targets of symlinks found inside a credential directory (#377).

A credential directory is emptied with a tmpfs, which hides a link inside it such as
``~/.aws/credentials -> /srv/x/cred``, but not the target: that stays readable at its own path. So
each masked credential directory is scanned (links are not followed) and the real target of
every link that leaves it is masked as well, through the same ``mask_target`` rule as the
directory itself (type, read and write roots, ``$HOME``, too-broad, dangling).

Decisions (integration owner, #377): a target under a package-managed system prefix (``/usr``,
``/nix/store``, ``/gnu/store``, ``/snap``) is public content and is left alone, since masking it
breaks ordinary links such as ``~/.config/tz -> /usr/share/zoneinfo``. A directory under ``/etc``
is shared configuration, so a link to one is refused; a file is masked. A dangling target is
refused when it lies under a read or write root of the run, and otherwise skipped with a count in
``Policy.notes`` (``DANGLING_NOTE``). That skip is a #353-kind residual: the policy binds the host
root read-only, so a target created while the run is in progress outside the private /run and /tmp
is readable at its own path. The scan reaches ``SCAN_DEPTH`` levels below each credential directory
(``.config/a/b/c``), where credential links live (``~/.ssh/id_*``, ``~/.aws/credentials``). Deeper
levels are counted in ``Policy.notes`` (``DEPTH_NOTE``): a residual, a link below them leaves its
target readable. Within that depth the scan stops at ``SCAN_LIMIT`` entries per directory and
refuses, as it does when a directory cannot be read (fail closed).
"""

from __future__ import annotations

import os
from collections.abc import Callable

from cairnwork_sandbox.backend import SandboxError
from cairnwork_sandbox.bwrap_reach import account_may_traverse

SCAN_LIMIT = 50_000
SCAN_DEPTH = 3
SYSTEM_PREFIXES = ("/usr/", "/nix/store/", "/gnu/store/", "/snap/")
ETC = "/etc/"
DANGLING_NOTE = "dangling credential links skipped, outside every read and write root: "
DEPTH_NOTE = f"credential directories not scanned for links below depth {SCAN_DEPTH}: "
VANISHED_NOTE = "credential subdirectories that vanished during the scan: "
_NULL = ["--ro-bind", "/dev/null"]


def inner_link_masks(
    cred_dirs: list[str],
    owner: bool,
    masked: list[str],
    mask: Callable[[str], list[str]],
    roots: list[str],
) -> tuple[list[str], tuple[str, ...]]:
    """bwrap flags masking the outside target of every link inside ``cred_dirs``, and the notes.

    ``masked`` is the credential masks already in the policy (``--tmpfs`` and ``/dev/null``
    binds): a target they already hide, or inside the scanned directory, is skipped. ``mask`` is
    ``mask_target`` bound to this policy. ``owner`` false is the dedicated account: a target is
    masked only when it may reach it (``account_may_traverse`` on the parent, #358); one it
    cannot reach cannot leak, and a mount there would fail. Each real target is masked once.
    ``roots``: a dangling target under one is refused (by ``mask``), any other skipped, counted."""
    dirs, files = _hidden(masked)
    flags: list[str] = []
    skipped = deep = gone = 0
    for cred_dir in cred_dirs:
        base = os.path.realpath(cred_dir)
        if not os.path.isdir(base):
            continue
        links, unscanned, vanished = _links_in(base)
        deep, gone = deep + unscanned, gone + vanished
        for link in links:
            real = os.path.realpath(link)
            if _covered(real, {base}) or _covered(real, dirs) or real in files:
                continue  # inside the scanned directory, or already masked
            if real.startswith(SYSTEM_PREFIXES) or not (
                owner or account_may_traverse(os.path.dirname(real))
            ):
                continue
            if not _stat_ok(link) and not _covered(real, set(roots)):
                skipped += 1
                continue
            if real.startswith(ETC) and os.path.isdir(real):
                raise SandboxError(
                    f"credential link {link!r} points to the /etc directory {real!r}; a mask there"
                    " would hide shared system configuration the run may need, so point the link"
                    " elsewhere"
                )
            added = mask(link)
            (dirs if added[0] == "--tmpfs" else files).add(added[-1])
            flags += added
    counts = ((DANGLING_NOTE, skipped), (DEPTH_NOTE, deep), (VANISHED_NOTE, gone))
    return flags, tuple(f"{note}{count}" for note, count in counts if count)


def _stat_ok(link: str) -> bool:
    """The link's target exists. Only ENOENT and ENOTDIR mean dangling; any other error (EACCES)
    is refused, since a target the owner cannot stat is not known to be absent."""
    try:
        os.stat(link)
    except (FileNotFoundError, NotADirectoryError):
        return False
    except OSError as err:
        raise SandboxError(
            f"credential link {link!r}: its target cannot be checked ({err.strerror}); a target"
            " that may exist but is unreadable is not skipped; make it readable or remove the link"
        ) from err
    return True


def _hidden(masked: list[str]) -> tuple[set[str], set[str]]:
    """The directories (``--tmpfs``) and files (``/dev/null`` binds) ``masked`` hides."""
    dirs = {masked[i + 1] for i, flag in enumerate(masked) if flag == "--tmpfs"}
    files = {masked[i + 2] for i, flag in enumerate(masked) if masked[i : i + 2] == _NULL}
    return dirs, files


def _covered(real: str, dirs: set[str]) -> bool:
    """``real`` is one of ``dirs`` or lies below one (checked by path component)."""
    current = real
    while True:
        if current in dirs:
            return True
        parent = os.path.dirname(current)
        if parent == current:
            return False
        current = parent


def _entry(e: os.DirEntry) -> tuple[str, bool, bool] | None:
    """(path, is_symlink, is_dir), or None if the child vanished; other errors refuse the scan."""
    try:
        return e.path, e.is_symlink(), e.is_dir(follow_symlinks=False)
    except (FileNotFoundError, NotADirectoryError):
        return None


def _links_in(base: str) -> tuple[list[str], int, int]:
    """Every symlink up to ``SCAN_DEPTH`` levels below ``base`` (links not followed), the count of
    directories at that depth unscanned, and of subdirectories that vanished. Refused past
    ``SCAN_LIMIT`` entries or when a directory cannot be scanned."""
    links: list[str] = []
    seen = unscanned = vanished = 0
    stack = [(base, 0)]
    while stack:
        current, depth = stack.pop()
        try:
            with os.scandir(current) as found:
                entries = [x for x in map(_entry, found) if x]
        except OSError as err:
            if depth and isinstance(err, FileNotFoundError | NotADirectoryError):
                vanished += 1  # gone: nothing left to leak
                continue
            raise SandboxError(
                f"credential directory {base!r}: cannot scan {current!r} for links"
                f" ({err.strerror}); a link left unscanned could leave its target readable"
            ) from err
        seen += len(entries)
        if seen > SCAN_LIMIT:
            raise SandboxError(
                f"credential directory {base!r} has more than {SCAN_LIMIT} entries within depth"
                f" {SCAN_DEPTH} to scan for links; move bulk data out of it"
            )
        for path, is_link, is_dir in entries:
            if is_link:
                links.append(path)
            elif is_dir and depth + 1 < SCAN_DEPTH:
                stack.append((path, depth + 1))
            elif is_dir:
                unscanned += 1
    return links, unscanned, vanished
