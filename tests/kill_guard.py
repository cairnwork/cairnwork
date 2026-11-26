"""Test-run guard for os.kill / os.killpg (#1849): a test may signal only its own descendants.

A scan once SIGKILLed a whole desktop. Under test, a non-zero signal reaches only the test
process itself or a process it started: a recorded Popen child, anything below the test
process in the process table, or a group or session a recorded child leads. A recorded child
counts only while it is unreaped (its number may be reused afterwards). Signal 0 only asks
whether a pid exists and always passes. A target that is already gone passes through, so the
real call raises the ProcessLookupError the code under test expects. The guard fails closed:
a process table that cannot be read (or lacks this process) refuses every non-zero signal.
Scope: only os.kill / os.killpg called inside the test process; sudo, /bin/kill and signals
sent by child processes are not covered.
"""

from __future__ import annotations

import os
import subprocess
from contextlib import suppress

_PS = "/bin/ps"


class KillGuardError(BaseException):  # noqa: N818 - BaseException: `except Exception` must not eat it
    """A test tried to signal a process that is not its own (or the table was unreadable)."""


REAL_KILL = os.kill  # for a test's own detached orphan, whose pid it read from its own file
_REAL_POPEN = subprocess.Popen  # a test may patch subprocess.Popen: the guard's ps must not


def _ps() -> tuple[int, str]:
    try:
        with _REAL_POPEN(  # noqa: S603 - fixed argv
            [_PS, "-axo", "pid=,ppid=,pgid="],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        ) as proc:
            try:
                out, _ = proc.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()
                raise
            return proc.returncode, out
    except (OSError, subprocess.SubprocessError) as exc:
        raise KillGuardError(f"process table unreadable ({exc!r}): refusing to signal") from exc


def _table() -> dict[int, tuple[int, int]]:
    code, text = _ps()
    out = text if code == 0 else ""
    rows: dict[int, tuple[int, int]] = {}
    for line in out.splitlines():
        fields = line.split()
        if len(fields) == 3 and all(f.isdigit() for f in fields):
            rows[int(fields[0])] = (int(fields[1]), int(fields[2]))
    if os.getpid() not in rows:  # a readable table always lists this very process
        raise KillGuardError("process table unreadable or incomplete: refusing to signal")
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


ACTIVE: list = []  # the guard installed for the running test (conftest)


def adopt(pid: int) -> int:
    """A test names a detached process of its own (a daemon its subprocess started, whose pid it
    read from its own state or pid file) so it may be signalled; returns the pid."""
    if ACTIVE:
        ACTIVE[-1].lineage.add(pid)
    return pid


class Guard:
    def __init__(self, real_kill, real_killpg) -> None:
        self.real_kill, self.real_killpg = real_kill, real_killpg
        self.started: list[subprocess.Popen] = []  # Popen children this process started
        self.lineage: set[int] = set()  # pids seen below this process (an orphan stays ours)

    def _look(self) -> dict[int, tuple[int, int]]:
        """Read the table and remember who is below this process right now: a descendant whose
        parent is later killed is reparented to launchd and no longer shows as one."""
        table = _table()
        self.lineage |= _descendants(table)
        return table

    def _own(self, table: dict[int, tuple[int, int]]) -> set[int]:
        reaped = {p.pid for p in self.started if p.pid in table and p.poll() is not None}
        return (_descendants(table) | self.lineage) - reaped

    def _live_started(self) -> set[int]:
        """Pids of recorded children that are still unreaped (their number is still theirs)."""
        return {p.pid for p in self.started if p.poll() is None}

    def _led_numbers(self, table: dict[int, tuple[int, int]]) -> set[int]:
        """Group numbers a recorded child leads: unreaped children, and reaped children whose
        number is still free in the table (a group outlives its leader, and the kernel does not
        reuse a number while a group has that id; a number the table lists as a process again is
        a stranger that reused it)."""
        return self._live_started() | {p.pid for p in self.started if p.pid not in table}

    def _mine(self, pid: int) -> bool:
        live = self._live_started()
        if pid == os.getpid() or pid in live:
            with suppress(KillGuardError):
                self._look()  # note the family before this signal may orphan part of it
            return True
        table = self._look()
        if pid not in table:
            return True  # gone: the real call raises ProcessLookupError
        own = self._own(table)
        led = {table[p][1] for p in own if p in table} | self._led_numbers(table)
        return pid in own or table[pid][1] in led - {os.getpgrp()}  # never our own group

    def kill(self, pid, sig):
        if sig != 0 and (not isinstance(pid, int) or pid <= 1 or not self._mine(pid)):
            raise KillGuardError(f"test signalled pid {pid!r} (signal {sig}): not its own")
        return self.real_kill(pid, sig)

    def killpg(self, group, sig):
        if sig != 0:
            ok = isinstance(group, int) and group > 1 and group != os.getpgrp()
            if ok and group in self._live_started():
                with suppress(KillGuardError):
                    self._look()
            elif ok:
                table = self._look()
                ok = (
                    not any(g == group for _p, g in table.values())
                    or group in self._led_numbers(table)
                    or any(table[p][1] == group for p in self._own(table) if p in table)
                )  # an empty group passes: the real call raises ProcessLookupError
            if not ok:
                raise KillGuardError(f"test signalled group {group!r} (signal {sig}): not its own")
        return self.real_killpg(group, sig)

    def record(self, init):
        def wrapped(popen, *args, **kwargs):
            init(popen, *args, **kwargs)
            self.started.append(popen)

        return wrapped
