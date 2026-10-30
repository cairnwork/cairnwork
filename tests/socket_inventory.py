"""Searchable-but-unlistable host directories the owner's socket walk may skip (#418).

connect() needs only search permission, so a socket under a directory the owner can search but
not list is reachable yet never probed. Such a directory passes only when it is in SEARCHABLE_OK
and a root listing of its whole subtree (sudo -n find, at test time) finds no socket beyond the
inventory evidenced for it. Those sockets are then probed by name. Without that listing (no
passwordless sudo, find failed) it fails like any other blind directory.
"""

import subprocess

# Each inventory is what a sudo listing on the ubuntu-24.04 runners found (PR 474 CI): the
# containerd sockets are root:root 0660, and neither the runner nor the verifier is in group root.
SEARCHABLE_OK = {
    "/run/containerd": ("/run/containerd/containerd.sock", "/run/containerd/containerd.sock.ttrpc"),
    "/run/libpod": (),  # 0751 root; podman state: alive, alive.lck, exits/ (0750)
    "/run/sudo": (),  # 0711 root, sudo's timestamp dir
}


def sudo_sockets(directory):
    """Every socket in ``directory``'s subtree as root sees it, or None without a clean listing."""
    try:
        done = subprocess.run(  # noqa: S603 - fixed argv
            ["sudo", "-n", "find", directory, "-xdev", "-type", "s", "-print0"],  # noqa: S607
            capture_output=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    return [p for p in done.stdout.decode(errors="surrogateescape").split("\0") if p]


def owner_sockets(host, blind, allowed=SEARCHABLE_OK, inventory=sudo_sockets):
    """(host sockets plus the evidenced ones to probe by name, failure lines) for blind dirs."""
    failures, named = [], []
    for path in blind:
        found = inventory(path) if path in allowed else None
        if found is None:
            failures.append("BLIND " + repr(path))
            continue
        extra = sorted(set(found) - set(allowed[path]))
        failures += ["BLIND-INVENTORY " + repr(p) for p in extra]
        named += [p for p in found if p in allowed[path]]
    return sorted(set(host) | set(named)), failures
