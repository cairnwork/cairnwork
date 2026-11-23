"""Masks for the outside targets of symlinks found inside a credential directory (#377).

A credential directory is emptied with a tmpfs, which hides a link inside it such as
``~/.aws/credentials -> /srv/x/cred``, but not the target: that stays readable at its own path. So
each masked credential directory is scanned (links are not followed) and the real target of
every link that leaves it is masked as well, through the same ``mask_target`` rule as the
directory itself (type, read and write roots, ``$HOME``, too-broad, dangling).

Decisions (integration owner, #377): a target under a package-managed system prefix (``/usr``,
``/nix/store``, ``/gnu/store``, ``/snap``) is public content and is left alone, since masking it
breaks ordinary links such as ``~/.config/tz -> /usr/share/zoneinfo`` and systemd user units. A
directory under ``/etc`` is shared system configuration the run may need, so a link to one is
refused by name, while a file there is masked. A dangling target is refused when it lies under a
read or write root of the run, and otherwise skipped with a count in ``Policy.notes``
(``DANGLING_NOTE``). That skip is a residual of the #353 kind, not a proof of safety: the policy
binds the whole host root read-only, so a target created while the run is in progress outside
the private /run and /tmp is readable (a socket connectable) at its own path. Refusing every
dangling target instead would block each run while, for example, a daemon whose socket a link
names (``~/.codex/.../app-server-control.sock -> /tmp/codex-daemon-*/...``) is not running. The
scan reaches ``SCAN_DEPTH`` levels below each credential directory (``.config/a/b/c``), where
credential links live (``~/.ssh/id_*``, ``~/.aws/credentials``, ``~/.config/gh/hosts.yml``);
a full scan of a ``.local/state`` can be millions of entries per run. Deeper levels are not
scanned and are counted in ``Policy.notes`` (``DEPTH_NOTE``): a residual, a link below them
leaves its target readable. Within that depth the scan stops at ``SCAN_LIMIT`` entries per
directory and refuses, as it does when a directory cannot be read (fail closed).
"""

from __future__ import annotations

import errno
import os
from collections.abc import Callable

from cairnwork_sandbox.backend import SandboxError
from cairnwork_sandbox.bwrap_reach import account_may_traverse

SCAN_LIMIT = 50_000
SCAN_DEPTH = 3
SCAN_ATTEMPTS = 3
SCAN_BASES = 64  # scan roots in all: the credential directories and outside link targets
_CHILD = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_CHANGED = (errno.ENOENT, errno.ENOTDIR, errno.ELOOP)
SYSTEM_PREFIXES = ("/usr/", "/nix/store/", "/gnu/store/", "/snap/")
ETC = "/etc/"
DANGLING_NOTE = "dangling credential links skipped (not refused, not proven safe): "
DEPTH_NOTE = f"credential directories not scanned for links below depth {SCAN_DEPTH}: "
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
    binds): a target they already hide is skipped, and so is one inside the directory being
    scanned. ``mask`` is ``mask_target`` bound to this policy. ``owner`` false is the dedicated
    account: a target is masked only when the account may reach it (``account_may_traverse`` on
    its parent, the #358 rule); one it cannot reach cannot leak to it, and a mount there would
    fail. Each real target is masked once. ``roots`` are the run's read and write roots: a
    dangling target under one is refused (by ``mask``), any other is skipped and counted."""
    dirs, files = _hidden(masked)
    flags: list[str] = []
    skipped = deep = 0
    pending = [os.path.realpath(path) for path in cred_dirs if os.path.isdir(path)]
    visited: set[str] = set()
    while pending:
        base = pending.pop()
        if base in visited:
            continue
        visited.add(base)
        if len(visited) > SCAN_BASES:
            raise SandboxError(f"credential links lead to more than {SCAN_BASES} scan roots")
        links, unscanned = _links_in(base)
        deep += unscanned
        for link in links:
            real = os.path.realpath(link)
            if _covered(real, {base}) or _covered(real, dirs) or real in files:
                continue  # inside the scanned directory, or already masked
            if real.startswith(SYSTEM_PREFIXES):
                continue
            if not owner and not account_may_traverse(os.path.dirname(real)):
                skipped += _covered(real, set(roots)) and not _stat_ok(link)  # #422, #1045
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
            if added[0] == "--tmpfs":
                pending.append(real)  # scan a masked outside directory too (#427)
    notes = [f"{DANGLING_NOTE}{skipped}"] if skipped else []
    return flags, tuple(notes + ([f"{DEPTH_NOTE}{deep}"] if deep else []))


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


def _links_in(base: str, attempts: int = SCAN_ATTEMPTS) -> tuple[list[str], int]:
    """Every symlink up to ``SCAN_DEPTH`` levels below ``base``, without following links, and the
    number of directories at that depth left unscanned; refused past ``SCAN_LIMIT`` entries or when
    a directory cannot be scanned. Depth first, each child opened by fd without following a link
    (#1314). A directory is (inode, device) from its listing and the opened fd must match; a listed
    directory must still be the one its path names; else scan again, up to ``attempts`` passes
    (#1354). A directory seen earlier but missing from the last pass (moved out) refuses."""
    seen_ids: set[tuple[int, int]] = set()
    last = OSError(errno.ENOENT, "no scan pass ran")
    for _ in range(attempts):
        ids: set[tuple[int, int]] = set()
        try:
            links, unscanned = _pass(base, ids)
            if seen_ids - ids:
                raise _Changed(errno.ENOENT, "directory swapped during the scan")
            return links, unscanned
        except OSError as err:
            last = err
            if err.errno not in _CHANGED:
                break
        seen_ids |= ids
    changing = "; the tree kept changing" if last.errno in _CHANGED else ""
    raise SandboxError(
        f"credential directory {base!r}: cannot scan it for links ({last.strerror}{changing});"
        " a link left unscanned could leave its target readable"
    ) from last


def _pass(base: str, ids: set[tuple[int, int]]) -> tuple[list[str], int]:
    """One pass of ``_links_in``; the identity of each directory listed goes to ``ids``."""
    links: list[str] = []
    count = [0, 0]  # entries seen, directories left unscanned

    def walk(path: str, depth: int, fd: int) -> None:
        st = os.fstat(fd)
        with os.scandir(fd) as found:
            entries = [
                (e.name, e.is_symlink(), e.inode() if e.is_dir(follow_symlinks=False) else 0)
                for e in found
            ]
        if not os.path.samestat(st, os.lstat(path)):
            raise _Changed(errno.ENOENT, "directory swapped during the scan")
        ids.update((ino, st.st_dev) for _, _, ino in entries if ino)
        count[0] += len(entries)
        if count[0] > SCAN_LIMIT:
            raise SandboxError(
                f"credential directory {base!r} has more than {SCAN_LIMIT} entries within"
                f" depth {SCAN_DEPTH} to scan for links; move bulk data out of it"
            )
        for name, is_link, ino in entries:
            if is_link:
                links.append(os.path.join(path, name))
            elif ino and depth + 1 < SCAN_DEPTH:
                child = os.open(name, _CHILD, dir_fd=fd)
                try:
                    got = os.fstat(child)
                    if got.st_dev != st.st_dev:
                        raise SandboxError(f"{path}/{name} in {base!r} is a mount point")
                    if got.st_ino != ino:  # not the directory the listing named
                        raise _Changed(errno.ENOENT, "directory swapped during the scan")
                    walk(os.path.join(path, name), depth + 1, child)
                finally:
                    os.close(child)
            elif ino:
                count[1] += 1

    root = os.open(base, _CHILD)
    try:
        ids.add(os.fstat(root)[1:3])
        walk(base, 0, root)
    finally:
        os.close(root)
    return links, count[1]


class _Changed(OSError):
    """A scanned directory was renamed, removed or retyped while it was being scanned."""
