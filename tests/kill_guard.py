"""Test-run guard for os.kill / os.killpg (#1849): a test may signal only its own descendants.

A scan once SIGKILLed a whole desktop. Under test, a non-zero signal reaches only the test
process itself or a process it started: a recorded Popen child, anything below the test
process in the process table, or a group or session a recorded child leads. Signal 0 only asks
whether a pid exists and always passes. A target that is already gone passes through, so the
real call raises the ProcessLookupError the code under test expects.
"""

from __future__ import annotations

import os
import subprocess

_PS = "/bin/ps"


class KillGuardError(RuntimeError):
    """A test tried to signal a process that is not its own."""


def _table() -> dict[int, tuple[int, int]]:
    out = subprocess.run(  # noqa: S603 - fixed argv
        [_PS, "-axo", "pid=,ppid=,pgid="], capture_output=True, text=True, check=False
    ).stdout
    rows: dict[int, tuple[int, int]] = {}
    for line in out.splitlines():
        fields = line.split()
        if len(fields) == 3 and all(f.isdigit() for f in fields):
            rows[int(fields[0])] = (int(fields[1]), int(fields[2]))
    return rows


def _descendants(table: dict[int, tuple[int, int]]) -> set[int]:
    found = {os.getpid()}
    grew = True
    while grew:
        grew = False
        for pid, (ppid, _pgid) in table.items():
            if ppid in found and pid not in found:
                found.add(pid)
                grew = True
    return found


class Guard:
    def __init__(self, real_kill, real_killpg) -> None:
        self.real_kill, self.real_killpg = real_kill, real_killpg
        self.started: set[int] = set()  # pids of Popen children this process started

    def _mine(self, pid: int) -> bool:
        if pid == os.getpid() or pid in self.started:
            return True
        table = _table()
        if pid not in table:
            return True  # gone: the real call raises ProcessLookupError
        own = _descendants(table)
        led = {table[p][1] for p in own if p in table} | self.started
        return pid in own or table[pid][1] in led - {os.getpgrp()}  # never our own group

    def kill(self, pid, sig):
        if sig != 0 and (not isinstance(pid, int) or pid <= 1 or not self._mine(pid)):
            raise KillGuardError(f"test signalled pid {pid!r} (signal {sig}): not its own")
        return self.real_kill(pid, sig)

    def killpg(self, group, sig):
        if sig != 0:
            ok = isinstance(group, int) and group > 1 and group != os.getpgrp()
            if ok and group not in self.started:
                table = _table()
                ok = not any(g == group for _p, g in table.values()) or any(
                    table[p][1] == group for p in _descendants(table) if p in table
                )  # an empty group passes: the real call raises ProcessLookupError
            if not ok:
                raise KillGuardError(f"test signalled group {group!r} (signal {sig}): not its own")
        return self.real_killpg(group, sig)

    def record(self, init):
        def wrapped(popen, *args, **kwargs):
            init(popen, *args, **kwargs)
            self.started.add(popen.pid)

        return wrapped
