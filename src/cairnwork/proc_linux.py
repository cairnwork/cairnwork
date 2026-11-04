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
        try:
            environment = _linux_environment(base)
        except (FileNotFoundError, ProcessLookupError):
            continue  # gone
        rows.append((int(name), pgid, uid, environment))
    return rows


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
