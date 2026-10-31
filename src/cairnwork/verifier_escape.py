"""Tell a harmless launchd system daemon from a process the candidate left behind (cairnwork#37).

On a host with a full Xcode, running /usr/bin/git goes through xcrun/xcodebuild, and launchd starts
the account's own distnoted (ppid 1). It is harmless and killed with everything else, but
VerifierAccount must not count it as an escape. A process is exempt only by its REAL executable path
(proc_pidpath, never the forgeable argv[0]/ps-comm), its parent being launchd (ppid 1), and an Apple
code signature — so a candidate that renames itself to a daemon, or re-parents to launchd while
running another binary, keeps its own real path and is still counted.

The real path is read after the process list, so a listed process can start a child and then
become the daemon in between; the child is not on that list. A count that is zero only because of
an exemption therefore lists the account's processes again; if the new list shows a process the
earlier one did not, the new list is checked the same way (at most LIST_ROUNDS rounds, after
which a list that is still changing gives "cannot tell"). A process is identified by pid, ppid and
start time, so a pid that was reused between two lists reads as a new process.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable

SYSTEM_DAEMONS = frozenset({"/usr/sbin/distnoted"})
LIST_ROUNDS = 3  # launchd may restart the daemon once or twice between two lists
Proc = tuple[int, int, str]  # (pid, ppid, start time as ps prints it)
PS_FIELDS = "pid=,ppid=,lstart="
PS_ENV = {"LC_ALL": "C"}  # lstart is strftime %c: in the C locale always five words, in English
# Printed, as the account, "pid NUL real-path NUL" per queried pid (a path can hold a newline or a
# tab but never a NUL, so no path can reshape the records). proc_pidpath returns the running
# image's vnode path from the kernel, which the target process cannot forge.
PIDPATH_HELPER = (
    "import ctypes,sys\n"
    "libc=ctypes.CDLL(None)\n"
    "libc.proc_pidpath.restype=ctypes.c_int\n"
    "b=ctypes.create_string_buffer(4096)\n"
    "for a in sys.argv[1:]:\n"
    "    n=libc.proc_pidpath(int(a),b,4096)\n"
    "    if n>0: sys.stdout.write('%s\\0%s\\0'%(a,b.value[:n].decode('utf-8','replace')))\n"
)


def apple_signed(path: str) -> bool:
    """True only if ``path`` is a genuine Apple-OS binary (the 'anchor apple' requirement, which a
    third-party Developer ID signature does not satisfy). Reads the file on disk, as the owner."""
    try:
        done = subprocess.run(  # noqa: S603 - fixed argv, path from proc_pidpath
            ["/usr/bin/codesign", "--verify", "-R=anchor apple", "--", path],
            capture_output=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


def is_exempt(real_path: str | None, ppid: int) -> bool:
    """Whether a process is a launchd system daemon that must not count as an escape: its real
    executable path is allow-listed, its parent is launchd (ppid 1), and it is Apple-signed."""
    return ppid == 1 and real_path in SYSTEM_DAEMONS and apple_signed(real_path)


def parse_pidpaths(out: str, queried: list[int]) -> dict[int, str] | None:
    """Parse PIDPATH_HELPER output strictly: NUL-terminated ``pid``, ``path`` fields. Anything off
    (truncated, odd field count, non-decimal or repeated pid, a pid that was not queried, empty
    path) is None, which the caller counts as "cannot tell", never as an exemption. A queried pid
    with no record is fine: it has no known path, so it is counted."""
    if not out:
        return {}
    if not out.endswith("\0"):
        return None
    fields = out[:-1].split("\0")
    if len(fields) % 2:
        return None
    allowed = set(queried)
    paths: dict[int, str] = {}
    for pid_s, path in zip(fields[0::2], fields[1::2], strict=True):
        if not (pid_s.isascii() and pid_s.isdigit()) or not path:
            return None
        pid = int(pid_s)
        if pid not in allowed or pid in paths:
            return None
        paths[pid] = path
    return paths


def parse_ps(out: str) -> list[Proc] | None:
    """Parse ``ps -o pid=,ppid=,lstart=`` under PS_ENV (``123 1 Sat Oct 31 14:26:01 2026``): the
    start time, to the second, keeps a reused pid apart from the process that had it before. Blank
    lines are skipped; any other line that is not two decimals and a five-word time is None
    ("cannot tell"), never a process left out of the count."""
    procs: list[Proc] = []
    for line in out.split("\n"):
        parts = line.split()
        if not parts:
            continue
        if len(parts) != 7 or not all(p.isascii() and p.isdigit() for p in parts[:2]):
            return None
        procs.append((int(parts[0]), int(parts[1]), " ".join(parts[2:])))
    return procs


def escaped_count(
    list_procs: Callable[[], list[Proc] | None],
    real_paths: Callable[[list[int]], dict[int, str] | None],
    darwin: bool,
) -> int:
    """How many processes of ``list_procs()`` are not an exempt daemon (-1: cannot tell). Only a
    launchd child (ppid 1) can be exempt and proc_pidpath is macOS-only, so ``real_paths`` is asked
    ONLY for those pids, never off Darwin. A zero that rests on an exemption is confirmed by a
    later list (module docstring); an unreadable list or unreadable paths are -1."""
    procs = list_procs()
    for _ in range(LIST_ROUNDS):
        if procs is None:
            return -1
        launchd = [pid for pid, ppid, _start in procs if ppid == 1]
        if not launchd or not darwin:
            return len(procs)
        real = real_paths(launchd)
        if real is None:
            return -1
        exempt = sum(1 for pid in launchd if is_exempt(real.get(pid), 1))
        if exempt < len(procs):
            return len(procs) - exempt
        again = list_procs()  # the paths were read after the list: anything new since?
        if again is not None and set(again) <= set(procs):
            return 0
        procs = again
    return -1
