"""Tie an owner process whose environment macOS withholds to an attempt by its cwd (#301).

macOS 27 withholds the environment of Apple platform binaries (``/bin/sh``, ``/bin/sleep``)
from ``ps -E`` and ``KERN_PROCARGS2``, even from the same user, so ``confined.sweep_marked``
cannot see the attempt's marker on them. Their working directory stays readable
(``proc_pidinfo``). Recovery passes the attempt's own run directories (``attempt_dirs``) and the
sweep kills such a process of the owner when its working directory is inside one of them
(``kill_withheld_in``).

Limits: a process that changed directory out of them is not found; neither is one found by a
second recovery after the first removed the directories (the first sweeps before removing).
A legitimately empty environment counts as withheld and is then tied only by its directory;
a shell (Cairnwork's own parent included) sitting in an attempt's run directory with a
withheld environment would be killed by recovery: run directories are Cairnwork scratch. An
attempt id that itself looks like a run directory name (``a1-q-v0``, or a blackbox clone's
``a1-v-vbb``) overlaps with ``a1``; real ids are ``a`` + 16 hex with no ``-``
(``pipeline.py``), so this cannot happen for them and the effect stays inside the run root.

Nothing here keeps or shows an environment: ``environment_withheld`` only counts its entries.
Off macOS the process readers answer None and nothing is killed; ``attempt_dirs`` is neutral.
"""

from __future__ import annotations

import ctypes
import functools
import os
import re
import signal
import stat
import sys
from pathlib import Path

from cairnwork.workspace import ATTEMPT_ID

_MACOS = sys.platform == "darwin"
_CTL_KERN, _KERN_ARGMAX, _KERN_PROCARGS2 = 1, 8, 49  # <sys/sysctl.h>
_PROC_PIDTBSDINFO, _PROC_PIDVNODEPATHINFO = 3, 9  # <sys/proc_info.h>
# struct proc_bsdinfo is 136 bytes; pbi_start_tvsec and pbi_start_tvusec are its last two uint64s
_BSDINFO_SIZE, _BSDINFO_START = 136, 120
_CS_OPS_STATUS, _CS_PLATFORM_BINARY = 0, 0x04000000  # <sys/codesign.h>
# struct proc_vnodepathinfo: pvi_cdir then pvi_rdir, each a vnode_info_path (a 152-byte
# vnode_info, then vip_path[MAXPATHLEN = 1024])
_VNODE_INFO_SIZE, _MAXPATHLEN = 152, 1024
_VNODEPATHINFO_SIZE = 2 * (_VNODE_INFO_SIZE + _MAXPATHLEN)


@functools.cache
def _system():
    lib = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
    size_p = ctypes.POINTER(ctypes.c_size_t)
    lib.sysctl.argtypes = [ctypes.POINTER(ctypes.c_int), ctypes.c_uint, ctypes.c_void_p, size_p,
                           ctypes.c_void_p, ctypes.c_size_t]  # fmt: skip
    lib.sysctl.restype = ctypes.c_int
    lib.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p,
                                 ctypes.c_int]  # fmt: skip
    lib.proc_pidinfo.restype = ctypes.c_int
    lib.csops.argtypes = [ctypes.c_int, ctypes.c_uint, ctypes.c_void_p, ctypes.c_size_t]
    lib.csops.restype = ctypes.c_int
    return lib


def _sysctl(names: tuple[int, ...], size: int) -> bytes | None:
    mib = (ctypes.c_int * len(names))(*names)
    buf = ctypes.create_string_buffer(size)
    length = ctypes.c_size_t(size)
    if _system().sysctl(mib, len(names), buf, ctypes.byref(length), None, 0) != 0:
        return None
    return buf.raw[: length.value]


@functools.cache
def _argmax() -> int | None:
    raw = _sysctl((_CTL_KERN, _KERN_ARGMAX), ctypes.sizeof(ctypes.c_int))
    return int.from_bytes(raw, sys.byteorder, signed=True) if raw else None


def environment_withheld(pid: int) -> bool | None:
    """True when KERN_PROCARGS2 shows the process's environment with no entry, False when it
    shows any, None when it cannot be read (gone, refused, malformed, or not macOS).

    XNU hides a platform binary's environment by skipping every NUL after the exec path, so
    one started with an empty argv[0] and one or more entries shows exactly one entry: the
    buffer alone looks like a readable one-entry environment. The code-signing status tells
    them apart (#310): a platform binary (``CS_PLATFORM_BINARY``) showing one entry or none
    counts as withheld. A process that is not one keeps the buffer's answer, so a readable
    one-entry environment of an ordinary program is never taken for withheld. On a macOS that
    shows platform binaries' environments, one with exactly one real entry counts as withheld
    too: it is then tied only by its directory (run directories are Cairnwork scratch)."""
    if not _MACOS or pid <= 0:
        return None
    try:
        size = _argmax()
        raw = _sysctl((_CTL_KERN, _KERN_PROCARGS2, pid), size) if size and size > 0 else None
    except (OSError, AttributeError, ValueError):
        return None
    count = _environment_count(raw) if raw else None
    if count is None:
        return None
    if count == 1:  # one entry may be the one an empty argv[0] lets through (#310)
        return _platform_binary(pid)
    return count == 0


def _platform_binary(pid: int) -> bool | None:
    """Whether the process is an Apple platform binary (csops CS_OPS_STATUS), None when the
    status cannot be read."""
    flags = ctypes.c_uint32(0)
    try:
        if _system().csops(pid, _CS_OPS_STATUS, ctypes.byref(flags), ctypes.sizeof(flags)) != 0:
            return None
    except (OSError, AttributeError):
        return None
    return bool(flags.value & _CS_PLATFORM_BINARY)


def _environment_count(raw: bytes) -> int | None:
    """How many environment strings a KERN_PROCARGS2 buffer holds: int argc, the exec path
    NUL-padded to a multiple of 8 bytes, argc argument strings, then the environment up to an
    empty string. None when the layout does not hold. Only counts: no string is kept."""
    if len(raw) < 4:
        return None
    argc = int.from_bytes(raw[:4], sys.byteorder, signed=True)
    end = raw.find(b"\0", 4)
    if argc < 0 or end < 0:
        return None
    # A fixed skip, not "skip every NUL": an empty argv[0] is a NUL too (#301 review).
    at = 4 + (end - 4 + 8) // 8 * 8
    if at > len(raw) or raw[end:at].strip(b"\0"):
        return None
    for _ in range(argc):
        end = raw.find(b"\0", at)
        if end < 0:
            return None
        at = end + 1
    count = 0
    while at < len(raw):
        end = raw.find(b"\0", at)
        if end == at:  # the empty string that ends the environment
            break
        count += 1
        at = len(raw) if end < 0 else end + 1
    return count


def cwd_of(pid: int) -> str | None:
    """The process's working directory (proc_pidinfo PROC_PIDVNODEPATHINFO), or None when it
    cannot be read (gone, refused, empty, or not macOS)."""
    if not _MACOS or pid <= 0:
        return None
    buf = ctypes.create_string_buffer(_VNODEPATHINFO_SIZE)
    try:
        done = _system().proc_pidinfo(pid, _PROC_PIDVNODEPATHINFO, 0, buf, _VNODEPATHINFO_SIZE)
    except (OSError, AttributeError):
        return None
    if done < _VNODEPATHINFO_SIZE:
        return None
    path = buf.raw[_VNODE_INFO_SIZE : _VNODE_INFO_SIZE + _MAXPATHLEN].split(b"\0", 1)[0]
    return os.fsdecode(path) or None


def _started(pid: int) -> bytes | None:
    """The process's start time (proc_pidinfo PROC_PIDTBSDINFO), None when unreadable: read
    before and after the other reads, an unchanged value means the pid was not reused."""
    buf = ctypes.create_string_buffer(_BSDINFO_SIZE)
    try:
        done = _system().proc_pidinfo(pid, _PROC_PIDTBSDINFO, 0, buf, _BSDINFO_SIZE)
    except (OSError, AttributeError):
        return None
    if done < _BSDINFO_SIZE:
        return None
    return buf.raw[_BSDINFO_START:_BSDINFO_SIZE]


def attempt_dirs(roots, attempt_id: str) -> tuple[Path, ...]:
    """Realpaths of the attempt's own run directories under each root: ``<attempt_id>`` and
    ``<attempt_id>-<tag>-v<n>[.suffix]`` (the part after ``-v`` may be digits or ``bb``). Only
    real directories (never a symlink) whose realpath is strictly inside the root's; a malformed
    attempt id gives none. Never raises: a root that is not a directory or cannot be listed is
    skipped, and so is an entry that cannot be checked."""
    if not isinstance(attempt_id, str) or not ATTEMPT_ID.fullmatch(attempt_id):
        return ()
    name = re.compile(rf"{re.escape(attempt_id)}-[vbpqre]-v([0-9]+|bb)(\.[^/]*)?")
    found: list[Path] = []
    for root in roots:
        try:
            base = os.path.realpath(root)
            if not os.path.isdir(base):
                continue
            here = []
            with os.scandir(base) as entries:
                for entry in entries:
                    if entry.name != attempt_id and not name.fullmatch(entry.name):
                        continue
                    try:
                        if not stat.S_ISDIR(os.lstat(entry.path).st_mode):  # a symlink is not
                            continue
                        real = os.path.realpath(entry.path)
                    except OSError:  # gone since the listing, or unreadable: skip only it
                        continue
                    if real != base and os.path.commonpath([real, base]) == base:
                        here.append(Path(real))
        except (OSError, ValueError):
            continue
        found += here
    return tuple(dict.fromkeys(found))


def _inside(cwd: str | None, dirs: list[str]) -> bool:
    """Whether the working directory the process reports, or its realpath now, is inside one of
    ``dirs`` (#599): a leftover may since have made the reported path resolve elsewhere. The
    reported path is taken as given only when absolute and without "." or "..", so its spelling
    alone never places a directory outside ``dirs`` inside one."""
    if not cwd:
        return False
    paths = [os.path.realpath(cwd)]
    if os.path.isabs(cwd) and os.path.normpath(cwd) == cwd:
        paths.append(cwd)
    for top in dirs:
        for path in paths:
            try:
                if os.path.commonpath([path, top]) == top:
                    return True
            except ValueError:
                continue
    return False


def kill_withheld_in(rows, dirs, mine: int) -> tuple[list[int], list[int], int]:
    """SIGKILL each of the owner's (``mine``) readable rows whose environment is withheld and
    whose working directory is inside one of ``dirs``; (killed pids, refused pids, how many had
    exited before the kill). ``dirs`` must be realpaths taken before the run started, while its
    processes could not yet change them (recovery: ``attempt_dirs``, real directories only, never
    a symlink): they are used as given, never resolved again here, so a directory the run later
    swaps for a symlink cannot move the sweep elsewhere (#578). Per row: the start time,
    environment_withheld, cwd_of, the start time again, then the kill, no wait in between; a
    changed or unreadable start time means the pid may now be another process, so it is left
    alone (#310). Once its start time was read, a later read that answers None (a
    process that ended reads so) or a changed start time counts as vanished, as
    ``confined._kill_marked`` counts a marked one: it may have left a child this look missed, so
    ``confirm_gone`` needs another clean look (#546). A start time unreadable at the first read is
    not counted: any process of the owner that ended after the listing reads so, and counting
    those kept a busy host from ever showing the attempt gone."""
    killed: list[int] = []
    denied: list[int] = []
    vanished = 0
    if not dirs or not _MACOS:
        return killed, denied, vanished
    tops = [os.fspath(d) for d in dirs]  # realpaths taken earlier: not followed again (#578)
    for pid, _pgid, uid, environment in rows:
        if uid != mine or environment is None:
            continue
        started = _started(pid)
        if started is None:  # gone since the listing (or unreadable): nothing ties it to them
            continue
        withheld = environment_withheld(pid)
        if withheld is False:
            continue
        cwd = cwd_of(pid) if withheld else None
        if cwd is not None and not _inside(cwd, tops):
            continue
        if cwd is None or _started(pid) != started:
            vanished += 1  # ended (or the pid was reused) during the reads
            continue
        try:
            os.kill(pid, signal.SIGKILL)
            killed.append(pid)
        except ProcessLookupError:
            vanished += 1  # exited after the listing: a child it started may be unlisted
        except PermissionError:
            denied.append(pid)
    return killed, denied, vanished
