"""When processes started, from the kernel, for the sweep (#244).

An attempt's marker (confined.ATTEMPT_ENV) is put in the environment when a process of the
attempt is spawned, and every one is spawned after the attempt's DISPATCHED entry. So a process
whose environment cannot be read but that the kernel shows started before the attempt cannot
carry the marker (confined.sweep_marked ``since``). Linux: btime from /proc/stat plus field 22
(starttime, clock ticks after boot) of /proc/<pid>/stat; macOS: ``ps -o lstart=`` (local time,
whole seconds). A start time that cannot be read is left out, so the process still counts.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

PS_TIMEOUT_SECONDS = 60


def not_older(pids: list[int], cutoff: float) -> list[int]:
    """The pids not shown to have started before cutoff (an unknown start time is kept)."""
    starts = start_times(pids)
    return [pid for pid in pids if not (pid in starts and starts[pid] < cutoff)]


def start_times(pids: list[int]) -> dict[int, float]:
    """{pid: start time, seconds since the epoch} of those the kernel tells; whole seconds."""
    if sys.platform.startswith("linux"):
        return linux_start_times(pids)
    try:
        done = subprocess.run(  # noqa: S603 - fixed argv, pids are integers
            ["/bin/ps", "-o", "pid=,lstart=", "-p", ",".join(map(str, pids))],
            capture_output=True,
            text=True,
            timeout=PS_TIMEOUT_SECONDS,
            check=False,
            env={"LC_ALL": "C", "PATH": "/usr/bin:/bin"},  # lstart in English, local time
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    found = {}
    for line in done.stdout.splitlines():
        pid, _, start = line.strip().partition(" ")
        try:
            found[int(pid)] = time.mktime(time.strptime(start.strip(), "%a %b %d %H:%M:%S %Y"))
        except (ValueError, OverflowError):
            continue
    return found


def linux_start_times(pids: list[int], proc: str = "/proc") -> dict[int, float]:
    """btime (/proc/stat) plus field 22 of /proc/<pid>/stat (clock ticks after boot)."""
    try:
        with open(f"{proc}/stat") as handle:
            btime = next(int(line.split()[1]) for line in handle if line.startswith("btime "))
        ticks = os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError, IndexError, StopIteration):
        return {}
    found = {}
    for pid in pids:
        try:
            with open(f"{proc}/{pid}/stat") as handle:
                after = handle.read().rsplit(")", 1)[1].split()  # field 3 onwards
            found[pid] = btime + int(after[19]) / ticks  # field 22: starttime
        except (OSError, ValueError, IndexError):
            continue
    return found
