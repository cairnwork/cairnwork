"""When processes started, counted from boot, for the sweep (#244).

An attempt's marker (confined.ATTEMPT_ENV) is put in the environment when a process of the
attempt is spawned, and every one is spawned after the attempt's DISPATCHED entry. So a process
whose environment cannot be read but that the kernel shows started before the attempt cannot
carry the marker (confined.sweep_marked ``dispatched``).

Linux only, and only on boot-relative clocks, which the wall clock does not move: DISPATCHED
journals boot_mark(), this boot's id and CLOCK_BOOTTIME; a process's start is field 22 of
/proc/<pid>/stat (clock ticks after boot, the same clock). The filter applies only when the
mark is from the current boot. No mark (macOS, an older journal, an unreadable boot id), a mark
from another boot, or a start time that cannot be read: the process still counts (the strict
rule). macOS reports process start times only on the wall clock, and no test or CI job there
needs the filter, so it is off there.
"""

from __future__ import annotations

import math
import os
import sys
import time

LINUX = sys.platform.startswith("linux")
BOOT_ID = "sys/kernel/random/boot_id"


def boot_mark(proc: str = "/proc") -> str:
    """``"<boot id> <seconds since boot>"`` now; "" off Linux or if either cannot be read."""
    clock = getattr(time, "CLOCK_BOOTTIME", None)  # Linux only
    boot = _boot_id(proc)
    if not LINUX or clock is None or not boot:
        return ""
    try:
        return f"{boot} {time.clock_gettime(clock)!r}"
    except OSError:
        return ""


def not_older(pids: list[int], mark: str, margin: float, proc: str = "/proc") -> list[int]:
    """The pids not shown to have started more than margin seconds before the mark was taken.

    All of them unless the mark is from this boot; an unreadable start time keeps the pid."""
    dispatched = _dispatched(mark, proc)
    if dispatched is None:
        return list(pids)
    cutoff = dispatched - margin
    kept = []
    for pid in pids:
        start = boot_start(pid, proc)
        if start is None or start >= cutoff:
            kept.append(pid)
    return kept


def boot_start(pid: int, proc: str = "/proc") -> float | None:
    """Seconds after boot the process started: field 22 of /proc/<pid>/stat; None if unknown.

    The fields are counted after the LAST ")", since the command name (field 2) may hold any."""
    try:
        with open(f"{proc}/{pid}/stat") as handle:
            after = handle.read().rsplit(")", 1)[1].split()  # field 3 onwards
        return int(after[19]) / os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError, IndexError):
        return None


def _dispatched(mark: str, proc: str) -> float | None:
    """The mark's seconds since boot, if it was taken in the current boot; else None."""
    if not LINUX or not mark:
        return None
    boot, _, at = mark.partition(" ")
    try:
        dispatched = float(at)
    except ValueError:
        return None
    if not boot or boot != _boot_id(proc) or not math.isfinite(dispatched):
        return None
    return dispatched


def _boot_id(proc: str) -> str:
    """This boot's id (/proc/sys/kernel/random/boot_id); "" if it cannot be read."""
    try:
        with open(f"{proc}/{BOOT_ID}") as handle:
            boot = handle.read().strip()
    except OSError:
        return ""
    return boot if boot and " " not in boot else ""
