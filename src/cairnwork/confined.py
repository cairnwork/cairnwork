"""Run one confined command: own process group, output copied by the parent, capped.

The child never holds the log's file descriptor; after it exits, its process
group is killed and any process that escaped the group but still carries the
run token in its environment is killed too (and reported, so the run fails).
"""

from __future__ import annotations

import os
import pwd
import resource
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from cairnwork import proc_restrict

RUN_TOKEN_VAR = "CAIRNWORK_VERIFIER_RUN"
ATTEMPT_ENV = "CAIRNWORK_ATTEMPT"  # marks every process of an attempt, for recovery
MAX_LOG_BYTES = 4 << 20
MAX_REPORT_BYTES = 4 << 20
# Reading the process list is a probe, not a verdict (Issue #114): a host under heavy load can
# take longer than a short timeout. The account probe asks again, but only after a kill.
PS_ATTEMPTS = 3
PS_TIMEOUT_SECONDS = 60


def execute(
    argv, cwd, env, timeout, log_path, reap=None, pass_fds=()
) -> tuple[int | None, bool, int]:
    """Run in a new process group; the parent copies output into log_path (the child
    never holds the log's file descriptor); kill the group afterwards."""
    token = env.get(RUN_TOKEN_VAR)
    if not token:
        raise ValueError(f"{RUN_TOKEN_VAR} must be set so escaped processes can be found")
    fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as log:
        process = subprocess.Popen(  # noqa: S603 - argv built from an allow-listed spec, no shell
            argv,
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            pass_fds=pass_fds,  # descriptors the child inherits on purpose (report_channel)
            preexec_fn=_limit_file_size,  # noqa: PLW1509 - only setrlimit, no locks
        )
        copier = threading.Thread(target=copy_capped, args=(process.stdout, log), daemon=True)
        copier.start()
        timed_out = False
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
        # Another account's processes: the owner cannot signal them; reap() kills them
        # all and says how many were running (the main one, if it timed out, included).
        try:
            escaped = reap() if reap is not None else 0
        finally:
            _kill_group(process.pid)
            process.wait()
        survivors = _sweep_survivors(token, process.pid) + escaped
        copier.join(timeout=10)  # a descendant that escaped the group may hold the pipe
        if process.stdout is not None:  # always: stdout=PIPE
            process.stdout.close()
    return (None if timed_out else process.returncode), timed_out, survivors


def _sweep_survivors(token: str, group: int) -> int:
    found = sweep_marked(RUN_TOKEN_VAR, token, skip_group=group)
    return found.found if found.listed else 1  # unknown counts as a survivor, so does a denied kill


SWEEP_UNAVAILABLE = -1  # recovery.Decision.killed_processes when the table could not be read
CONFIRM_TRIES = 10  # looks after a kill until the killed processes are shown gone (confirm_gone)
CONFIRM_WAIT = 0.1
HIDDEN_TRIES = 3  # re-reads of a process whose environment was hidden (fork/exec hides it briefly)
HIDDEN_WAIT = 0.05


@dataclass(frozen=True)
class Sweep:
    """One look at the process table for a marker (#244). Only ``gone`` means "none left":
    a delivered SIGKILL is not a death (uninterruptible I/O), and a process whose environment
    cannot be read may carry the marker."""

    listed: bool  # the whole table, and the worker account's uid when asked, could be read
    killed: int = 0  # marked processes SIGKILL was delivered to: alive when listed
    denied: int = 0  # marked processes whose kill was refused: still running
    unreadable: int = 0  # the owner's processes whose environment could not be read
    account: int = 0  # processes of the worker account (environments the owner cannot read)
    pids: tuple[int, ...] = ()  # the processes behind those counts, for the operator (describe)
    restricted: str = ""  # not listed because /proc hides other users' processes (hidepid)

    @property
    def found(self) -> int:
        return self.killed + self.denied

    @property
    def gone(self) -> bool:
        return self.listed and not (self.found or self.unreadable or self.account)

    def problem(self) -> str:
        """Why the processes are not shown gone, starting with a stable code ("" when gone)."""
        if self.restricted:
            return (f"SWEEP_UNAVAILABLE: process table restricted ({self.restricted}): the worker"
                    " account's processes cannot all be listed and may still run")  # fmt: skip
        if not self.listed:
            return "SWEEP_UNAVAILABLE: processes of this attempt were not checked and may still run"
        if self.found:
            return (f"SURVIVORS_REMAIN: {self.found} process(es) of the attempt still listed"
                    f" ({self.denied} refused the kill)")  # fmt: skip
        if self.unreadable:
            return (f"UNREADABLE_PROCESSES: {self.unreadable} of the owner's processes hide their"
                    " environment and may belong to the attempt")  # fmt: skip
        if self.account:
            return (f"WORKER_ACCOUNT_BUSY: the worker account runs {self.account} process(es),"
                    " which the owner cannot inspect")  # fmt: skip
        return ""


UNLISTED = Sweep(listed=False)


def sweep_marked(
    var: str, value: str, *, skip_group: int | None = None, account: str | None = None
) -> Sweep:
    """SIGKILL the owner's processes whose environment holds var=value, and say what was seen.

    Used for processes that escaped a run's process group (e.g. via setsid) and for a
    dead run's workers. Members of skip_group were just sent SIGKILL and are not
    counted. A process that clears its environment is not found; only a separate
    uid stops that. ``account``: a worker account the attempt ran as; its processes cannot
    be inspected, so each one is counted (``Sweep.account``). A table (or an account) that
    cannot be read is UNLISTED: "may still be running", never "none". A process whose owner
    cannot be read (Linux: pgid None) may be the owner's or the account's: it is judged as one
    of the owner's, so a hidden environment keeps it unreadable (#244).
    """
    marker = f"{var}={value}"
    theirs = _account_uid(account) if account else None
    processes = _processes_with_environment()
    if processes is None or (account and theirs is None):
        return UNLISTED
    mine, me = os.getuid(), os.getpid()
    rows = [r for r in processes if r[0] != me and (skip_group is None or r[1] != skip_group)]
    held = [r[0] for r in rows if theirs is not None and r[2] in (theirs, FOREIGN)]
    ours = [r for r in rows if r[2] == mine and r[2] != theirs]  # another user's: not ours
    # Kill what the listing shows marked before re-reading the hidden ones: the re-read waits,
    # and a listed pid may meanwhile be reused by an unrelated process (#244 NIT).
    killed, denied = _kill_marked([r for r in ours if r[3] is not None], marker)
    hidden = _resolve_hidden([r for r in ours if r[3] is None], mine)
    late_killed, late_denied = _kill_marked([r for r in hidden if r[3] is not None], marker)
    killed, denied = killed + late_killed, denied + late_denied
    unreadable = [r[0] for r in hidden if r[3] is None]
    pids = tuple(sorted({*killed, *denied, *unreadable, *held}))
    # the owner's own marked processes are killed above even when the account's may be hidden
    hidden_by = _restricted() if theirs is not None else ""  # hidepid never hides one's own
    return Sweep(not hidden_by, len(killed), len(denied), len(unreadable), len(held), pids,
                 hidden_by)  # fmt: skip


def _kill_marked(rows, marker: str) -> tuple[list[int], list[int]]:
    """(pids SIGKILL was delivered to, pids that refused it) of the rows that carry marker."""
    killed, denied = [], []
    for pid, _pgid, _uid, environment in rows:
        if marker not in environment:
            continue
        try:
            os.kill(pid, signal.SIGKILL)
            killed.append(pid)
        except ProcessLookupError:
            pass  # already gone
        except PermissionError:
            denied.append(pid)  # still running (NIT #244: a refused kill is a survivor)
    return killed, denied


def confirm_gone(first: Sweep, again: Callable[[], Sweep]) -> Sweep:
    """Look again (``again``) after ``first`` until the processes are shown gone, the table
    cannot be read, or CONFIRM_TRIES run out; return the last look."""
    found = first
    for _ in range(CONFIRM_TRIES):
        if found.gone or not found.listed:
            break
        time.sleep(CONFIRM_WAIT)  # SIGKILLed: wait until the kernel has ended them
        found = again()
    return found


def _resolve_hidden(rows, mine: int):
    """Read again the owner's processes whose environment was hidden: one that is starting
    (fork, exec) hides it for a moment, and a busy host nearly always has one. Those that
    exited are dropped; those still hidden after HIDDEN_TRIES stay hidden (None)."""
    for _ in range(HIDDEN_TRIES):
        hidden = {row[0] for row in rows if row[2] == mine and row[3] is None}
        if not hidden:
            break
        time.sleep(HIDDEN_WAIT)
        again = _reread(hidden)
        if again is None:  # unknown: they stay hidden
            continue
        rows = [
            (pid, pgid, uid, again[pid] if pid in hidden else env)
            for pid, pgid, uid, env in rows
            if pid not in hidden or pid in again
        ]
    return rows


def _reread(pids: set[int]) -> dict[int, set[str] | None] | None:
    """{pid: environment, or None if still hidden} of those still running; None if unknown."""
    if sys.platform.startswith("linux"):
        found = {}
        for pid in pids:
            try:
                found[pid] = _linux_environment(f"/proc/{pid}")
            except (FileNotFoundError, ProcessLookupError):
                continue  # gone
        return found
    try:
        done = subprocess.run(  # noqa: S603 - fixed argv, pids are integers
            ["/bin/ps", "-wwE", "-o", _PS_COLUMNS, "-p", ",".join(map(str, sorted(pids)))],
            capture_output=True,
            text=True,
            timeout=PS_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode == 1 and not done.stdout.strip() and not done.stderr.strip():
        return {}  # none of them is running any more
    if done.returncode != 0:
        return None
    return {pid: env for pid, _pgid, _uid, env in _parse_ps(done.stdout) if pid in pids}


def _restricted() -> str:
    """Why /proc may hide another user's processes; "" when it cannot (#244, proc_restrict)."""
    return proc_restrict.restriction() if sys.platform.startswith("linux") else ""


def _account_uid(account: str) -> int | None:
    try:
        return pwd.getpwnam(account).pw_uid
    except KeyError:
        return None


def _processes_with_environment():
    """[(pid, pgid, effective uid, set of 'NAME=value' tokens or None if unreadable)], or None
    if the table cannot be read. A zombie runs nothing: ps rows of one are left out, and on
    Linux its environment reads empty."""
    if sys.platform.startswith("linux"):
        return _linux_processes()
    # One attempt, no retry: this sweep kills by token only after the read, so waiting for a
    # second answer would give an escaped process time to undo its traces. A slow or failed read
    # stays unknown, which counts as a survivor (Issue #114).
    try:
        done = subprocess.run(  # noqa: S603 - fixed argv
            ["/bin/ps", "-axwwE", "-o", _PS_COLUMNS],
            capture_output=True,
            text=True,
            timeout=PS_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0 or not done.stdout.strip():
        return None
    return _parse_ps(done.stdout)


_PS_COLUMNS = "pid=,pgid=,uid=,stat=,command="


def _parse_ps(stdout: str):
    rows = []
    for line in stdout.splitlines():
        fields = line.split(None, 4)
        if len(fields) < 4 or not all(f.isdigit() for f in fields[:3]) or "Z" in fields[3]:
            continue
        command = fields[4] if len(fields) == 5 else ""
        # ps prints "(name)" when it cannot read a process's arguments and environment
        hidden = command.startswith("(") and command.endswith(")")
        rows.append((int(fields[0]), int(fields[1]), int(fields[2]),
                     None if hidden else set(command.split())))  # fmt: skip
    return rows


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
            # status hidden (e.g. hidepid=1): no process group, so no skip_group drops it (#244)
            try:
                uid, pgid = _hidden_owner(base, int(name)), None
            except (FileNotFoundError, ProcessLookupError):
                continue  # gone
        try:
            environment = _linux_environment(base)
        except (FileNotFoundError, ProcessLookupError):
            continue  # gone
        rows.append((int(name), pgid, uid, environment))
    return rows


FOREIGN = -1  # a process that is not the owner's, of a uid that cannot be told (_hidden_owner)


def _hidden_owner(base: str, pid: int) -> int:
    """The owner of a process whose status cannot be read (hidepid=1 hides other users'). Its
    /proc directory belongs to its effective uid, or to root when it is not dumpable: then a
    signal 0 tells one the owner may signal (its own: judged as the owner's) from one it may not
    (FOREIGN: root's or any other user's, the worker account's included). A directory that
    cannot be read either is judged as the owner's, so a hidden environment keeps it unreadable."""
    mine = os.getuid()
    try:
        owner = _dir_owner(base)
    except (FileNotFoundError, ProcessLookupError):
        raise
    except OSError:
        return mine
    if owner != 0 or mine == 0:
        return owner
    try:
        os.kill(pid, 0)  # no signal is sent: only the permission is checked
    except PermissionError:
        return FOREIGN
    return mine


def _dir_owner(base: str) -> int:
    return os.stat(base).st_uid


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


def copy_capped(source, target) -> None:
    """Copy at most MAX_LOG_BYTES + 1 bytes (one over the cap marks the log too big)."""
    written = 0
    for chunk in iter(lambda: source.read(1 << 16), b""):
        keep = chunk[: max(0, MAX_LOG_BYTES + 1 - written)]
        if keep:
            target.write(keep)
            written += len(keep)


def _kill_group(pgid: int) -> None:
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass  # the group is already gone


def _limit_file_size() -> None:
    """In the child: no file it writes (log, report, scratch) may exceed the caps, and
    the files it creates are group-readable whatever the caller's umask (the report
    of a verifier that runs as another account must be readable by the owner)."""
    os.umask(0o022)
    limit = MAX_LOG_BYTES + MAX_REPORT_BYTES
    resource.setrlimit(resource.RLIMIT_FSIZE, (limit, limit))
