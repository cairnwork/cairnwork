"""A test that makes a detached process of its own (a daemon an intermediate process starts, then
exits) cannot name it before the code under test kills it. The test records the pid in a file;
this wraps os.kill so the file's pid, and what runs below it, is signalled with kill_guard.REAL_KILL
(meant for a test's own detached orphan whose pid it read from its own file) the moment the code
under test signals it. Not adopt(): ps lstart can read up to 1 s before the test began on Linux."""

import os
from pathlib import Path

import kill_guard


def _family(pid_files) -> set[int]:
    roots: set[int] = set()
    for path in pid_files:
        try:
            roots.add(int(Path(path).read_text()))
        except (OSError, ValueError):
            continue
    try:
        table = kill_guard._table()
    except kill_guard.KillGuardError:
        return set()
    roots &= set(table)  # a root that is gone may have its number reused: not ours any more
    grew = True
    while grew:
        grew = False
        for pid, (ppid, _pgid) in table.items():
            if ppid in roots and pid not in roots:
                roots.add(pid)
                grew = True
    return roots


def adopt_on_kill(monkeypatch, *pid_files) -> None:
    """Let os.kill reach the pids named in ``pid_files`` (and their descendants)."""
    guarded = os.kill

    def kill(pid, sig):
        if sig != 0 and isinstance(pid, int) and pid in _family(pid_files):
            return kill_guard.REAL_KILL(pid, sig)
        return guarded(pid, sig)

    monkeypatch.setattr(os, "kill", kill)
