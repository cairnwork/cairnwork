"""A private, read-only /run (and a separate /var/run) for each bwrap run (#391).

A read-only root bind does not stop ``connect()`` on a pathname socket, and /run holds the host's
daemon sockets (journald, systemd notify and varlink, snapd, uuidd, D-Bus, container daemons). So
each run gets an empty tmpfs there, in its own mount namespace (a socket the host creates later
does not appear either), made read-only last. Put back: each top-level symlink of the tree
(NixOS ``/run/current-system``); each top-level ``/etc`` symlink to a regular file under a tree
(``/etc/resolv.conf``), read-only, under the dedicated account only one it may reach (else the
bind fails the run); each read root strictly under a tree, read-only (one that is or holds a
tree would bring the host tree back, and the root bind gives it anyway). Write roots are bound
after these flags. A tree or ``/etc`` that cannot be listed fails the policy (``SandboxError``).
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


def trees() -> list[str]:
    """The real path of each tree that exists, once (``/var/run`` is usually a link to /run)."""
    found: list[str] = []
    for tree in TREES:
        real = os.path.realpath(tree)
        if os.path.isdir(tree) and _NAME.fullmatch(real) and real not in found:
            found.append(real)
    return found


def _under(path: str, roots: list[str]) -> bool:
    return any(path.startswith(root + "/") for root in roots)


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


def private_flags(reads: list[str], owner: bool) -> tuple[list[str], list[str]]:
    """(the flags that follow the root bind, the ``--remount-ro`` flags that end the policy)."""
    private = trees()
    head: list[str] = []
    for tree in private:
        head += ["--tmpfs", tree]
        for entry in _entries(tree):
            target = _readlink(entry)
            if _NAME.fullmatch(target) and _NAME.fullmatch(entry.name):
                head += ["--symlink", target, f"{tree}/{entry.name}"]
    for entry in _entries(ETC):
        real = os.path.realpath(entry.path) if entry.is_symlink() else ""
        if not _under(real, private) or not _NAME.fullmatch(real) or real in head:
            continue
        try:
            regular = stat.S_ISREG(os.stat(real).st_mode)
        except OSError:
            continue
        if regular and (owner or account_may_traverse(os.path.dirname(real))):
            head += ["--ro-bind", real, real]
    head += [flag for root in reads if _under(root, private) for flag in ("--ro-bind", root, root)]
    return head, [flag for tree in private for flag in ("--remount-ro", tree)]
