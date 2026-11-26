"""A test that makes a detached process of its own (a daemon an intermediate process starts, then
exits) cannot name it before the code under test kills it. The test records the pid in a file;
this wraps os.kill so the file's pid, and what runs below it, is signalled with kill_guard.REAL_KILL
(meant for a test's own detached orphan whose pid it read from its own file) the moment the code
under test signals it. Not adopt(): ps lstart can read up to 1 s before the test began on Linux."""

import os
from pathlib import Path

import kill_guard


def _family(pid_files, seen: dict[int, int]) -> set[int]:
    """The pids the files name and what runs below them. ``seen`` (pid -> start time) is kept by
    the caller: a child whose parent the code under test killed first is reparented to launchd and
    no longer shows below it, but it stays ours while it keeps the start time first seen."""
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
    starts = kill_guard._starts()
    roots &= set(table)  # a root that is gone may have its number reused: not ours any more
    roots = {p for p in roots if p not in seen or starts.get(p) == seen[p]}
    family = set(roots)
    grew = True
    while grew:
        grew = False
        for pid, (ppid, _pgid) in table.items():
            if ppid in family and pid not in family:
                family.add(pid)
                grew = True
    for pid in family:
        if pid in starts:
            seen[pid] = starts[pid]
    return family | {p for p, start in seen.items() if p in table and starts.get(p) == start}


def adopt_on_kill(monkeypatch, *pid_files) -> None:
    """Let os.kill reach the pids named in ``pid_files`` (and their descendants)."""
    guarded = os.kill
    seen: dict[int, int] = {}

    def kill(pid, sig):
        if sig != 0 and isinstance(pid, int) and pid in _family(pid_files, seen):
            return kill_guard.REAL_KILL(pid, sig)
        return guarded(pid, sig)

    monkeypatch.setattr(os, "kill", kill)


def adopt_started_in_test(monkeypatch) -> None:
    """For a detached process whose pid no file can name (a sandboxed run's grandchild): when the
    code under test signals a pid, adopt() it first, so it passes only if it started after the
    test began. The code under test picks its targets by its own run token, not this helper."""
    guarded = os.kill

    def kill(pid, sig):
        if sig != 0 and isinstance(pid, int):
            kill_guard.adopt(pid)
        return guarded(pid, sig)

    monkeypatch.setattr(os, "kill", kill)
