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
import pwd
import re
import signal
import stat
import struct
import sys
import tempfile
import time
from pathlib import Path

from cairnwork import survivor_argv
from cairnwork.run_tags import RUN_TAGS
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
    tags = rf"(?:[{re.escape(RUN_TAGS)}]|b2)"  # #1190: "-b2" = second-seed base runs before "s"
    name = re.compile(rf"{re.escape(attempt_id)}-{tags}-v([0-9]+|bb)(\.[^/]*)?")
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


_TEMP_SHAPE = re.compile(r"^/private/var/folders/[^/]+/[^/]+/T$", re.IGNORECASE)
_VERIFIER_BASE = "/users/shared/cairnwork"  # verifier_account's macOS base (casefolded)
_DARWIN = sys.platform == "darwin"  # the real platform: tests flip _MACOS alone to fake macOS


class OutOfScope(ValueError):
    """A well-formed path (or root) outside what a sweep may touch: kill_withheld_in skips it,
    confined.execute and agy refuse to start (a malformed one is a plain ValueError)."""


def run_mark() -> float:
    """The wall clock now, taken before a run starts: a process that began after it is the run's."""
    return time.time()


def _epoch(raw: bytes | None) -> float | None:
    """``_started``'s bytes (pbi_start_tvsec, pbi_start_tvusec) as seconds; None if unreadable."""
    if raw is None or len(raw) != 16:
        return None
    sec, usec = struct.unpack("<QQ", raw)
    return sec + usec / 1e6


def _temps() -> list[str]:
    """The user's private temp dir. macOS: only ``/private/var/folders/<b>/<id>/T``, never the
    shared ``/tmp`` or ``/var/tmp``; elsewhere (nothing is swept there) the temp dir as set."""
    real = os.path.realpath(tempfile.gettempdir())
    return [real] if not (_MACOS and _DARWIN) or _TEMP_SHAPE.match(real) else []


def _home() -> str:
    """Home from the account database, never ``$HOME``."""
    return os.path.realpath(pwd.getpwuid(os.getuid()).pw_dir)


def dirs_since(dirs) -> float | None:
    """The earliest st_birthtime of the run dirs; None (sweep off) if none or unreadable."""
    try:  # getattr: st_birthtime is not on every platform's stat_result
        births = [getattr(os.stat(d), "st_birthtime", None) for d in dirs]
    except OSError:
        return None
    return None if not births or None in births else min(births)


def _canon(item) -> str:
    """One path (str, bytes or PathLike) as an absolute spelling without "." or ".." parts, or
    ValueError; the sweep is scoped by what this returns, never by a second reading."""
    text = os.fsdecode(os.fspath(item))
    parts = text.split("/")
    if not text.startswith("/") or any(p in (".", "..") for p in parts):
        raise ValueError(f"sweep directory must be absolute without . or ..: {text!r}")
    return "/" + "/".join(p for p in parts if p)


def _below(path: str, top: str, depth: int = 1) -> bool:
    """Whether ``path`` is ``depth`` levels below ``top`` (casefolded: APFS)."""
    low, base = path.casefold(), top.casefold().rstrip("/")
    return low.startswith(base + "/") and len(low[len(base) :].split("/")) > depth


def _root_ok(root: str) -> bool:
    """A declared root is narrow: inside the private temp dir, three levels below home (never
    home, ``~/workspace`` or ``~/workspace/<repo>``), or inside the verifier account's base."""
    home = _home()
    return (
        any(_below(root, t) for t in _temps())
        or (home not in ("", "/") and _below(root, home, 3))
        or _below(root, _VERIFIER_BASE)
    )


def _collection(value, what: str) -> tuple:  # one str or bytes is not a collection
    if isinstance(value, (str, bytes, bytearray)) or not hasattr(value, "__iter__"):
        raise ValueError(f"sweep {what} must be a collection of paths, not one path")
    return tuple(value)  # a one-shot iterable is read once


def scope(dirs, roots=(), resolve: bool = False) -> tuple[str, ...]:
    """The sweep directories, checked and returned as ONE tuple the caller then uses (a check on
    one value and a kill on another was the gap): each, by spelling and by realpath, must be
    strictly inside the private temp dir or a ``roots`` entry the caller declared (itself narrow,
    ``_root_ok``); else ValueError (OutOfScope when only the place is wrong; a lone str or bytes,
    a relative path or ".." is plain). ``resolve``: return the realpaths (before the run)."""
    given = tuple(_canon(d) for d in _collection(dirs, "directories"))
    tops = tuple(os.path.realpath(_canon(r)) for r in _collection(roots, "roots"))
    if any(not _root_ok(r) for r in tops):
        raise OutOfScope("sweep root is not a narrow run root")
    real = tuple(os.path.realpath(d) for d in given)  # a link out of a root is judged by its target
    temps = _temps()
    for path in {*real, *given}:
        if not (any(_below(path, t) for t in temps) or any(_below(path, r) for r in tops)):
            raise OutOfScope(f"sweep directory is not inside a declared run root: {path!r}")
    return real if resolve else given


def entry_dirs(cwd, roots=()) -> tuple[str, ...]:
    """A run's sweep directory (realpath, before it starts): ``scope`` on macOS, where it runs."""
    return scope([cwd], roots, resolve=True) if _MACOS else (os.path.realpath(cwd),)


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


def kill_withheld_in(
    rows, dirs, mine: int, seen=None, roots=(), since: float | None = None
) -> tuple[list[int], list[int], int]:
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
    not counted (a process that ended after the listing reads so; counting those kept a busy host
    from ever showing the attempt gone). ``seen``: each killed process's allow-listed argv prefix,
    read just before the kill (#814). ``roots``: the run roots the caller declares; each of
    ``dirs`` must be inside the private temp dir or one of them (``scope``), else only it is
    skipped (#1849); a malformed one raises ValueError before any signal. ``since`` (before the
    run started): only a process that began after it is killed; None: nothing is swept (fail
    closed); 0.0: no lower bound."""
    killed: list[int] = []
    denied: list[int] = []
    vanished = 0
    if not _MACOS:
        return killed, denied, vanished
    tops: list[str] = []  # one value for check and use; never followed again (#578)
    roots = _collection(roots, "roots")  # read once, not once per directory
    for one in _collection(dirs, "directories"):  # a malformed one raises, before any signal
        try:
            tops += scope([one], roots)
        except OutOfScope:
            continue  # only this directory is skipped (#1849)
    if not tops or since is None:
        return killed, denied, vanished
    for pid, _pgid, uid, environment in rows:
        if uid != mine or environment is None:
            continue
        started = _started(pid)
        if started is None:  # gone since the listing (or unreadable): nothing ties it to them
            continue
        if since:  # 0.0: no lower bound; else gone, unreadable or older than the run: not its own
            began = _epoch(started)
            if began is None or began <= since:
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
        if seen is not None:
            survivor_argv.note(seen, pid)
        try:
            os.kill(pid, signal.SIGKILL)
            killed.append(pid)
        except ProcessLookupError:
            vanished += 1  # exited after the listing: a child it started may be unlisted
        except PermissionError:
            denied.append(pid)
    return killed, denied, vanished
