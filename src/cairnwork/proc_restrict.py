"""Whether Linux /proc may hide other users' processes (hidepid), for the sweep (#244).

Under ``hidepid=1`` other users' process details cannot be read; under ``hidepid=2`` (or
``invisible``, ``noaccess``, ``ptraceable``) their directories are not even listed. A sweep that
must see a worker account's processes then cannot tell "idle" from "hidden", so it fails closed
(confined.sweep_marked: not listed, with this reason). hidepid never hides the caller's own
processes; a same-user sweep still reads their owners through the /proc directory and a
signal-0 probe (confined._hidden_owner), so it is not refused for hidepid. A ``gid=`` exemption
is not trusted: any hidepid other than 0/off counts as restricted. Mount options that cannot be
read (no mountinfo, or no /proc mount in it) count as restricted too: no probe can tell, e.g.,
a PID namespace whose pid 1 is the owner's own process from an open /proc.
"""

from __future__ import annotations

_OPEN = frozenset({"0", "off"})


def restriction(mountinfo: str = "/proc/self/mountinfo") -> str:
    """Why other users' processes may be hidden (e.g. "hidepid=2 on /proc"); empty if none."""
    try:
        with open(mountinfo) as handle:
            options = _proc_options(handle.read())
    except OSError:
        options = None
    if options is None:
        return "mount options of /proc unreadable"
    hidden = [o for o in options if o.startswith("hidepid=") and o[8:] not in _OPEN]
    return f"{hidden[-1]} on /proc" if hidden else ""


def _proc_options(text: str) -> list[str] | None:
    """The options of the last mount on /proc (the one in effect), or None if there is none.
    mountinfo line: id parent maj:min root mountpoint options [optional...] - type source super."""
    found = None
    for line in text.splitlines():
        mine, sep, theirs = line.partition(" - ")
        fields, rest = mine.split(), theirs.split()
        if sep and len(fields) >= 6 and fields[4] == "/proc" and rest[:1] == ["proc"]:
            found = fields[5].split(",") + (rest[2].split(",") if len(rest) >= 3 else [])
    return found
