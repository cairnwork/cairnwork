"""Tell a harmless launchd system daemon from a process the candidate left behind (cairnwork#37).

On a host with a full Xcode, running /usr/bin/git goes through xcrun/xcodebuild, and launchd starts
the account's own distnoted (ppid 1). It is harmless and killed with everything else, but
VerifierAccount must not count it as an escape. A process is exempt only by its REAL executable path
(proc_pidpath, never the forgeable argv[0]/ps-comm), its parent being launchd (ppid 1), and an Apple
code signature — so a candidate that renames itself to a daemon, or re-parents to launchd while
running another binary, keeps its own real path and is still counted.

The real path is read after the process list, so a listed process can start a child and then
become the daemon in between; the child is not on that list. A count that is zero only because of
an exemption is therefore confirmed: the account's processes are listed again, and each exempt pid
still listed must show the same kernel start time (to the microsecond) and the same path as when
it was exempted. A process is identified by (pid, start time), so a pid that was reused reads as a
new process. Anything new or different is checked in a further round (at most LIST_ROUNDS rounds,
after which a list that is still changing gives "cannot tell"). A process that starts after the
confirming list is outside what one snapshot can show.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable

SYSTEM_DAEMONS = frozenset({"/usr/sbin/distnoted"})
LIST_ROUNDS = 3  # launchd may restart the daemon once or twice between two lists
Seen = tuple[str, str]  # (kernel start time "sec.usec", real executable path) of one pid
# Printed, as the account, "pid NUL start NUL real-path NUL" per queried pid (a path can hold a
# newline or a tab but never a NUL, so no path can reshape the records). proc_pidpath returns the
# running image's vnode path and proc_pidinfo(PROC_PIDTBSDINFO) the start time, both from the
# kernel, which the target process cannot forge. The start time is read before and after the path;
# a pid whose start differs, or whose start or path cannot be read, gets no record (it is counted).
PIDPATH_HELPER = r"""
import ctypes, sys
U = ctypes.c_uint32


class Info(ctypes.Structure):  # struct proc_bsdinfo, <sys/proc_info.h>
    _fields_ = [
        ("pbi_flags", U), ("pbi_status", U), ("pbi_xstatus", U), ("pbi_pid", U),
        ("pbi_ppid", U), ("pbi_uid", U), ("pbi_gid", U), ("pbi_ruid", U), ("pbi_rgid", U),
        ("pbi_svuid", U), ("pbi_svgid", U), ("rfu_1", U), ("pbi_comm", ctypes.c_char * 16),
        ("pbi_name", ctypes.c_char * 32), ("pbi_nfiles", U), ("pbi_pgid", U), ("pbi_pjobc", U),
        ("e_tdev", U), ("e_tpgid", U), ("pbi_nice", ctypes.c_int32),
        ("pbi_start_tvsec", ctypes.c_uint64), ("pbi_start_tvusec", ctypes.c_uint64),
    ]


libc = ctypes.CDLL(None)
libc.proc_pidinfo.restype = ctypes.c_int
libc.proc_pidinfo.argtypes = [
    ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int
]
libc.proc_pidpath.restype = ctypes.c_int


def start(pid):
    info = Info()
    n = libc.proc_pidinfo(pid, 3, 0, ctypes.byref(info), ctypes.sizeof(info))  # PROC_PIDTBSDINFO
    if n != ctypes.sizeof(info) or info.pbi_pid != pid or info.pbi_start_tvusec > 999999:
        return None
    return "%d.%06d" % (info.pbi_start_tvsec, info.pbi_start_tvusec)


b = ctypes.create_string_buffer(4096)
for a in sys.argv[1:]:
    pid = int(a)
    before = start(pid)
    n = libc.proc_pidpath(pid, b, 4096)
    if before is not None and n > 0 and start(pid) == before:
        sys.stdout.write("%d\0%s\0%s\0" % (pid, before, b.value[:n].decode("utf-8", "replace")))
"""


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


def _is_start(text: str) -> bool:
    sec, dot, usec = text.partition(".")
    digits = sec.isascii() and sec.isdigit() and usec.isascii() and usec.isdigit()
    return bool(dot) and digits and len(usec) == 6


def parse_pidpaths(out: str, queried: list[int]) -> dict[int, Seen] | None:
    """Parse PIDPATH_HELPER output strictly: NUL-terminated ``pid``, ``start``, ``path`` fields.
    Anything off (truncated, field count not a multiple of three, non-decimal or repeated pid, a pid
    that was not queried, a start that is not ``sec.usec`` with six usec digits, empty path) is
    None, which the caller counts as "cannot tell", never as an exemption. A queried pid with no
    record is fine: it has no known path, so it is counted."""
    if not out:
        return {}
    if not out.endswith("\0"):
        return None
    fields = out[:-1].split("\0")
    if len(fields) % 3:
        return None
    allowed = set(queried)
    seen: dict[int, Seen] = {}
    for pid_s, start, path in zip(fields[0::3], fields[1::3], fields[2::3], strict=True):
        if not (pid_s.isascii() and pid_s.isdigit()) or not _is_start(start) or not path:
            return None
        pid = int(pid_s)
        if pid not in allowed or pid in seen:
            return None
        seen[pid] = (start, path)
    return seen


def escaped_count(
    list_procs: Callable[[], list[tuple[int, int]] | None],
    real_paths: Callable[[list[int]], dict[int, Seen] | None],
    darwin: bool,
) -> int:
    """How many processes of ``list_procs()`` (pid, ppid) are not an exempt daemon (-1: cannot
    tell). Only a launchd child (ppid 1) can be exempt and proc_pidpath is macOS-only, so
    ``real_paths`` is asked ONLY for those pids, never off Darwin. A zero that rests on an exemption
    is confirmed as the module docstring says; an unreadable list or unreadable paths are -1."""
    procs = list_procs()
    for _ in range(LIST_ROUNDS):
        if procs is None:
            return -1
        launchd = [pid for pid, ppid in procs if ppid == 1]
        if not launchd or not darwin:
            return len(procs)
        seen = real_paths(launchd)
        if seen is None:
            return -1
        exempt = {pid: seen[pid] for pid in launchd if pid in seen and is_exempt(seen[pid][1], 1)}
        if len(exempt) < len(procs):
            return len(procs) - len(exempt)
        again = list_procs()  # the paths were read after the list: anything new since?
        if again is not None and set(again) <= set(procs):
            kept = [pid for pid in exempt if (pid, 1) in again]
            now = real_paths(kept) if kept else {}
            if now is not None and all(now.get(pid) == exempt[pid] for pid in kept):
                return 0
        procs = again
    return -1
