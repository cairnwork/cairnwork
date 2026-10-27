"""Tie an owner process whose environment macOS withholds to an attempt by its cwd (#301).

macOS 27 withholds the environment of Apple platform binaries (``/bin/sh``, ``/bin/sleep``)
from ``ps -E`` and ``KERN_PROCARGS2``, even from the same user, so ``confined.sweep_marked``
cannot see the attempt's marker on them. Their working directory stays readable
(``proc_pidinfo``). Recovery passes the attempt's own run directories (``attempt_dirs``) and the
sweep kills such a process of the owner when its working directory is inside one of them
(``kill_withheld_in``).

Limits: a process that changed directory out of them is not found, nor a platform binary
started with an empty argv[0] (see ``environment_withheld``); neither is one found by a
second recovery after the first removed the directories (the first sweeps before removing).
A legitimately empty environment counts as withheld and is then tied only by its directory;
a shell (Cairnwork's own parent included) sitting in an attempt's run directory with a
withheld environment would be killed by recovery: run directories are Cairnwork scratch. An
attempt id that itself looks like a run directory name (``a1-q-v0``) overlaps with ``a1``;
real ids are ``a`` + 16 hex, so the effect stays inside the run root.

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
_PROC_PIDVNODEPATHINFO = 9  # <sys/proc_info.h>
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

    Known gap: XNU hides a platform binary's environment by skipping every NUL after the exec
    path, so one started with an empty argv[0] and two or more entries shows its first entry.
    That buffer cannot be told apart from a readable one-entry environment, so it counts as
    shown (False) and is never killed by its directory. Cairnwork never starts an empty
    argv[0]."""
    if not _MACOS or pid <= 0:
        return None
    try:
        size = _argmax()
        raw = _sysctl((_CTL_KERN, _KERN_PROCARGS2, pid), size) if size and size > 0 else None
    except (OSError, AttributeError, ValueError):
        return None
    count = _environment_count(raw) if raw else None
    return None if count is None else count == 0


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
    if not cwd:
        return False
    real = os.path.realpath(cwd)
    for top in dirs:
        try:
            if os.path.commonpath([real, top]) == top:
                return True
        except ValueError:
            continue
    return False


def kill_withheld_in(rows, dirs, mine: int) -> tuple[list[int], list[int], int]:
    """SIGKILL each of the owner's (``mine``) readable rows whose environment is withheld and
    whose working directory is inside one of ``dirs``; (killed pids, refused pids, how many had
    exited before the kill). Per row: environment_withheld, cwd_of, then the kill, no wait in
    between. An unknown answer (None) is ignored: it cannot be tied to the attempt."""
    killed: list[int] = []
    denied: list[int] = []
    vanished = 0
    if not dirs or not _MACOS:
        return killed, denied, vanished
    tops = [os.path.realpath(d) for d in dirs]
    for pid, _pgid, uid, environment in rows:
        if uid != mine or environment is None or environment_withheld(pid) is not True:
            continue
        if not _inside(cwd_of(pid), tops):
            continue
        try:
            os.kill(pid, signal.SIGKILL)
            killed.append(pid)
        except ProcessLookupError:
            vanished += 1  # exited after the listing: a child it started may be unlisted
        except PermissionError:
            denied.append(pid)
    return killed, denied, vanished
