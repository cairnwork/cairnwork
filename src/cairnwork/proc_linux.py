"""Reading the Linux process table from /proc (moved unchanged out of confined.py, #814)."""

from __future__ import annotations

import os


def _linux_processes(proc: str = "/proc"):
    rows = []
    try:
        entries = [p for p in os.listdir(proc) if p.isdigit()]
    except OSError:
        return None
    for name in entries:
        base = f"{proc}/{name}"
        try:
            uid, pgid = _linux_owner(base)
        except (FileNotFoundError, ProcessLookupError):
            continue  # gone
        except (OSError, ValueError, IndexError, StopIteration):
            # owner unknown: it may be one of ours; no process group, so no skip_group drops
            # it (#244); a hidepid mount is refused as a whole (proc_restrict)
            uid, pgid = os.getuid(), None
        if _zombie(base):
            continue  # runs nothing; its environ cannot be opened by a non-root user
        try:
            environment = _linux_environment(base)
        except (FileNotFoundError, ProcessLookupError):
            continue  # gone
        rows.append((int(name), pgid, uid, environment))
    return rows


def _zombie(base: str) -> bool:
    """True only if the process is a zombie: its own stat says Z and every thread says Z.

    A process whose main thread has exited but whose other threads run shows Z on its own stat
    too, and is alive. Its own stat not being Z means alive, and the threads are not read. No
    task directory at all (FileNotFoundError): its own Z decides. Any other failure to list the
    threads, or an empty list: not a zombie (strict). The threads are listed again after the
    reads: a different set means one was made meanwhile, so it is not one. The state is the first field after the
    LAST ")" (the command name may hold any bytes); stat is read as bytes."""
    if _state(f"{base}/stat") != b"Z":
        return False
    try:
        tids = os.listdir(f"{base}/task")
    except FileNotFoundError:
        return True
    except OSError:
        return False
    if not tids or any(_state(f"{base}/task/{tid}/stat") != b"Z" for tid in tids):
        return False
    # a live thread may have made a new one and ended between the listing and the reads
    try:
        return set(os.listdir(f"{base}/task")) == set(tids)
    except FileNotFoundError:
        return True  # gone
    except OSError:
        return False


def _state(path: str) -> bytes | None:
    try:
        with open(path, "rb") as handle:
            return handle.read().rsplit(b")", 1)[1].split()[0]
    except (OSError, ValueError, IndexError):
        return None


def _linux_environment(base: str) -> set[str] | None:
    """None: another user's, or one of ours that hides it (not dumpable). Raises if gone."""
    try:
        with open(f"{base}/environ", "rb") as handle:
            return set(handle.read().decode("utf-8", "replace").split("\0"))
    except (FileNotFoundError, ProcessLookupError):
        raise
    except OSError:
        return None


def _linux_owner(base: str) -> tuple[int, int]:
    """(effective uid, process group) from status and stat, which every user can read: a
    process that is not dumpable shows root as the owner of its /proc directory."""
    with open(f"{base}/status") as handle:
        uid = next(int(line.split()[2]) for line in handle if line.startswith("Uid:"))
    with open(f"{base}/stat") as handle:
        pgid = int(handle.read().rsplit(")", 1)[1].split()[2])  # after "(comm)": state ppid pgrp
    return uid, pgid
