"""Searchable-but-unlistable host directories the owner's socket walk may skip (#418).

connect() needs only search permission, so a socket under a directory the owner can search but
not list is reachable yet never probed. Such a directory passes only when it is in SEARCHABLE_OK
and a root listing of its whole subtree (sudo -n find, at test time, across mounts) finds no
socket beyond the inventory evidenced for it and no symlink at all: connect() follows a symlink,
and its target is not resolved here, so any symlink fails. The evidenced sockets are then probed
by name. Without a clean listing (no passwordless sudo, find failed or wrote to stderr, timeout)
it fails like any other blind directory.
"""

import subprocess

# Each inventory is what a sudo listing on the ubuntu-24.04 runners found (PR 474 CI): the
# containerd sockets are root:root 0660, and neither the runner nor the verifier is in group root.
# Only sockets are evidenced, never a symlink.
SEARCHABLE_OK = {
    "/run/containerd": ("/run/containerd/containerd.sock", "/run/containerd/containerd.sock.ttrpc"),
    "/run/libpod": (),  # 0751 root; podman state: alive, alive.lck, exits/ (0750)
    "/run/sudo": (),  # 0711 root, sudo's timestamp dir
}


def root_find(directory, kind):
    """Paths of find type ``kind`` in ``directory``'s subtree as root sees it (mounts crossed, no
    link followed), or None without a clean listing: non-zero exit or any stderr output."""
    try:
        done = subprocess.run(  # noqa: S603 - fixed argv
            ["sudo", "-n", "find", directory, "-type", kind, "-print0"],  # noqa: S607
            capture_output=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0 or done.stderr:
        return None
    return [p for p in done.stdout.decode(errors="surrogateescape").split("\0") if p]


def sudo_sockets(directory):
    """(sockets, symlinks) in ``directory``'s subtree as root sees it, or None unless both
    listings are clean."""
    sockets, links = root_find(directory, "s"), root_find(directory, "l")
    return None if sockets is None or links is None else (sockets, links)


def owner_sockets(host, blind, allowed=SEARCHABLE_OK, inventory=sudo_sockets):
    """(host sockets plus the evidenced ones to probe by name, failure lines) for blind dirs."""
    failures, named = [], []
    for path in blind:
        listing = inventory(path) if path in allowed else None
        if listing is None:
            failures.append("BLIND " + repr(path))
            continue
        sockets, links = listing
        extra = sorted((set(sockets) - set(allowed[path])) | set(links))  # no link is evidenced
        failures += ["BLIND-INVENTORY " + repr(p) for p in extra]
        named += [p for p in sockets if p in allowed[path]]
    return sorted(set(host) | set(named)), failures
