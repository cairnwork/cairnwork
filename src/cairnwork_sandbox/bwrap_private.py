"""A private, read-only /run (and a separate /var/run) for each bwrap run (#391).

/run holds the host's daemon sockets (journald, systemd, snapd, uuidd, D-Bus, container daemons),
and a read-only bind does not stop ``connect()``. So each run gets an empty tmpfs there in its own
mount namespace (a socket the host adds later does not appear), read-only last. Put back: each
top-level link that resolves inside a tree or an immutable store (NixOS ``/run/current-system``;
not Rancher's ``/run/docker.sock`` -> home); each top-level ``/etc`` link to a regular file in a
tree (``/etc/resolv.conf``), read-only, for the account only one it may reach (a file swapped
after the check is bound as it then is). A read or write root in a tree is refused: none is put
there, and ``--remount-ro`` does not recurse into a write bind. A tree that cannot be listed or
written in the policy, or an /etc entry that cannot be checked, refuses the policy.
"""

from __future__ import annotations

import os
import re
import stat

from cairnwork_sandbox.backend import SandboxError
from cairnwork_sandbox.bwrap_reach import account_may_traverse

TREES = ("/run", "/var/run")  # read at call time: tests rebind it
ETC = "/etc"
_NAME = re.compile(r"^[A-Za-z0-9_./+@~][A-Za-z0-9 _./+@~-]*$")  # no line break, no leading '-'
_STORES = ["/nix/store", "/gnu/store"]  # immutable package stores: no socket lives there


def trees() -> list[str]:
    """The real path of each tree that exists, once (``/var/run`` is usually a link to /run)."""
    found: list[str] = []
    for tree in TREES:
        real = os.path.realpath(tree)
        if not os.path.isdir(tree) or real in found:
            continue
        if not _NAME.fullmatch(real):
            raise SandboxError(f"private tree {real!r} has characters not allowed in a policy")
        found.append(real)
    return found


def _under(path: str, roots: list[str]) -> bool:
    return any(path == root or path.startswith(root + "/") for root in roots)


def _entries(directory: str) -> list[os.DirEntry[str]]:
    try:
        with os.scandir(directory) as listing:
            return sorted(listing, key=lambda entry: entry.name)
    except OSError as err:
        raise SandboxError(f"cannot list {directory} to build the private /run: {err}") from err


def _readlink(entry: os.DirEntry[str]) -> str:
    try:
        return os.readlink(entry.path) if entry.is_symlink() else ""
    except OSError:  # removed meanwhile
        return ""


def private_flags(reads: list[str], writes: list[str], owner: bool) -> tuple[list[str], list[str]]:
    """(the flags that follow the root bind, the ``--remount-ro`` flags that end the policy)."""
    private = trees()
    for root in reads + writes:
        if _under(root, private):
            raise SandboxError(f"root {root!r} is in a private tree {private}")
    head: list[str] = []
    for tree in private:
        head += ["--tmpfs", tree]
        for entry in _entries(tree):
            target = _readlink(entry)
            inside = _under(os.path.realpath(entry.path), private + _STORES)
            if inside and _NAME.fullmatch(target) and _NAME.fullmatch(entry.name):
                head += ["--symlink", target, f"{tree}/{entry.name}"]
    for entry in _entries(ETC):
        try:
            real = os.path.realpath(entry.path) if entry.is_symlink() else ""
        except OSError as err:
            raise SandboxError(f"cannot check {entry.path} for the private /run: {err}") from err
        if not _under(real, private) or not _NAME.fullmatch(real) or real in head:
            continue
        try:
            regular = stat.S_ISREG(os.stat(real).st_mode)
        except OSError:
            continue
        if regular and (owner or account_may_traverse(os.path.dirname(real))):
            head += ["--ro-bind", real, real]
    return head, [flag for tree in private for flag in ("--remount-ro", tree)]
