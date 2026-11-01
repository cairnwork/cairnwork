"""Run one confined command: own process group, output copied by the parent, capped.

The child never holds the log's file descriptor; after it exits, its process
group is killed and any process that escaped the group but still carries the
run token in its environment is killed too (and reported, so the run fails).
"""

from __future__ import annotations

import os
import pwd
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, replace

from cairnwork import proc_cwd, proc_restrict, proc_start
from cairnwork.pipe_copy import MAX_LOG_BYTES as MAX_LOG_BYTES  # re-exported (verifiers)
from cairnwork.pipe_copy import MAX_REPORT_BYTES as MAX_REPORT_BYTES  # re-exported (report_guard)
from cairnwork.pipe_copy import _limit_file_size, copy_capped

RUN_TOKEN_VAR = "CAIRNWORK_VERIFIER_RUN"
ATTEMPT_ENV = "CAIRNWORK_ATTEMPT"  # marks every process of an attempt, for recovery
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
    mark = proc_start.boot_mark()  # before the start: what predates it is not the run's
    dirs = (os.path.realpath(cwd),) if cwd is not None else ()  # now: the run may swap it (#578)
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
        timed_out = finished = False
        try:  # the group is killed on any way out of the wait, an interrupt included (#537)
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
            # Another account's processes: the owner cannot signal them; reap() kills them
            # all and says how many were running (the main one, if it timed out, included).
            escaped = reap() if reap is not None else 0
            finished = True
        finally:
            _kill_group(process.pid)
            if not finished and reap is not None:  # interrupted: the account's processes too
                with suppress(Exception):  # best effort (own deadline); the interrupt propagates
                    reap()
            with suppress(subprocess.TimeoutExpired):  # bounded only after an interrupt
                process.wait(timeout=None if finished else 10)
        survivors = _sweep_survivors(token, process.pid, dirs, mark) + escaped
        copier.join(timeout=10)  # a descendant that escaped the group may hold the pipe
        if process.stdout is not None:  # always: stdout=PIPE
            process.stdout.close()
    return (None if timed_out else process.returncode), timed_out, survivors


def _sweep_survivors(token: str, group: int, dirs: tuple[str, ...] = (), mark: str = "") -> int:
    """Anything not shown gone counts (#578); a look that killed none and is not gone is redone."""
    found = sweep_marked(RUN_TOKEN_VAR, token, skip_group=group, dispatched=mark, cwd_dirs=dirs)
    if found.listed and not found.found and not found.gone:  # e.g. one exited: a child left?
        found = sweep_marked(RUN_TOKEN_VAR, token, skip_group=group, dispatched=mark, cwd_dirs=dirs)
    return 0 if found.gone else max(1, found.found)


SWEEP_UNAVAILABLE = -1  # recovery.Decision.killed_processes when the table could not be read
CONFIRM_TRIES = 10  # looks after a kill until the killed processes are shown gone (confirm_gone)
CONFIRM_WAIT = 0.1
HIDDEN_TRIES = 3  # re-reads of a process whose environment was hidden (fork/exec hides it briefly)
HIDDEN_WAIT = 0.05
START_MARGIN = 5.0  # seconds: a hidden process started this much before DISPATCHED is old


@dataclass(frozen=True)
class Sweep:
    """One look at the process table for a marker (#244). Only ``gone`` means "none left":
    a delivered SIGKILL is not a death (uninterruptible I/O), a process whose environment
    cannot be read may carry the marker, and a marked process that exited before its kill may
    have left a child this look missed (``vanished``; confirm_gone then needs two clean looks)."""

    listed: bool  # the whole table, and the worker account's uid when asked, could be read
    killed: int = 0  # marked (or attributed by cwd) processes SIGKILL was delivered to
    denied: int = 0  # marked (or attributed by cwd) processes whose kill was refused: running
    unreadable: int = 0  # the owner's processes whose environment could not be read
    account: int = 0  # processes of the worker account (environments the owner cannot read)
    held_by: tuple[tuple[int, str], ...] = ()  # (pid, KEPT_BY label) behind those counts
    restricted: str = ""  # not listed: Linux /proc may hide processes (hidepid, proc_restrict)
    vanished: int = 0  # marked processes that exited between the listing and their kill
    unconfirmed: bool = False  # clean, but not twice in a row within the window (confirm_gone)

    @property
    def found(self) -> int:
        return self.killed + self.denied

    @property
    def gone(self) -> bool:
        busy = self.found or self.unreadable or self.account or self.vanished or self.unconfirmed
        return self.listed and not busy

    def problem(self) -> str:
        """Why the processes are not shown gone, starting with a stable code ("" when gone)."""
        if self.restricted:
            return (f"SWEEP_UNAVAILABLE: process table restricted ({self.restricted}): processes"
                    " of this attempt may be hidden and may still run")  # fmt: skip
        if not self.listed:
            return "SWEEP_UNAVAILABLE: processes of this attempt were not checked and may still run"
        if self.found:
            return (f"SURVIVORS_REMAIN: {self.found} process(es) of the attempt still listed"
                    f" ({self.denied} refused the kill)")  # fmt: skip
        if self.vanished or self.unconfirmed:
            return ("SURVIVORS_REMAIN: the attempt was not listed gone twice in a row"
                    f" ({self.vanished} died before the kill, maybe leaving a child)")  # fmt: skip
        if self.unreadable:
            return (f"UNREADABLE_PROCESSES: {self.unreadable} of the owner's processes hide their"
                    " environment and may belong to the attempt")  # fmt: skip
        if self.account:
            return (f"WORKER_ACCOUNT_BUSY: the worker account runs {self.account} process(es),"
                    " which the owner cannot inspect")  # fmt: skip
        return ""

    def kept_by(self, limit: int = 20) -> str:
        """What keeps the fence, for the operator: pids and a fixed label each, never text a
        process supplies (its name or arguments could hold a secret)."""
        if self.restricted:
            return f"process table restricted ({self.restricted})"
        shown = ", ".join(f"{pid} {label}" for pid, label in self.held_by[:limit])
        more = len(self.held_by) - limit
        return shown + (f" and {more} more" if more > 0 else "")


KEPT_BY = ("marked", "cwd", "hidden", "worker-account")  # the labels of Sweep.held_by


UNLISTED = Sweep(listed=False)


def sweep_marked(
    var: str,
    value: str,
    *,
    skip_group: int | None = None,
    account: str | None = None,
    dispatched: str = "",
    cwd_dirs: tuple[str, ...] = (),
) -> Sweep:
    """SIGKILL the owner's processes whose environment holds var=value, and say what was seen.

    Used for processes that escaped a run's process group (e.g. via setsid) and for a
    dead run's workers. Members of skip_group were just sent SIGKILL and are not
    counted. A process that clears its environment is not found; only a separate
    uid stops that. ``account``: a worker account the attempt ran as; its processes cannot
    be inspected, so each one is counted (``Sweep.account``). A table (or an account) that
    cannot be read is UNLISTED: "may still be running", never "none". A process whose owner
    cannot be read (Linux: pgid None) may be the owner's or the account's: it is judged as one
    of the owner's, so a hidden environment keeps it unreadable (#244). ``dispatched``: the
    attempt's DISPATCHED boot mark; a hidden process started before it is not counted (proc_start).
    ``cwd_dirs`` (macOS): an attempt's run directories or a run's checkout, as realpaths taken
    before the run could swap one for a symlink (#578); an owner process whose environment macOS
    withholds (/bin/sh, /bin/sleep) is killed ("cwd") when its working directory is inside one;
    one that left them is not found (proc_cwd). Empty: no extra reads.
    """
    marker = f"{var}={value}"
    theirs = _account_uid(account) if account else None
    processes = _processes_with_environment()
    if processes is None or (account and theirs is None):
        return UNLISTED
    mine, me = os.getuid(), os.getpid()
    rows = [r for r in processes if r[0] != me and (skip_group is None or r[1] != skip_group)]
    held = [r[0] for r in rows if theirs is not None and r[2] == theirs]
    ours = [r for r in rows if r[2] == mine and r[2] != theirs]  # another user's: not ours
    # Kill what the listing shows marked before re-reading the hidden ones: the re-read waits,
    # and a listed pid may meanwhile be reused by an unrelated process (#244 NIT).
    killed, denied, vanished = _kill_marked([r for r in ours if r[3] is not None], marker)
    plain = [r for r in ours if r[3] is not None and marker not in r[3]] if cwd_dirs else []
    by_cwd = proc_cwd.kill_withheld_in(plain, cwd_dirs, mine) if cwd_dirs else ([], [], 0)
    hidden = _resolve_hidden([r for r in ours if r[3] is None], mine)
    late = _kill_marked([r for r in hidden if r[3] is not None], marker)
    killed, denied, vanished = killed + late[0], denied + late[1], vanished + late[2] + by_cwd[2]
    unreadable = [r[0] for r in hidden if r[3] is None]
    if dispatched and unreadable:
        unreadable = proc_start.not_older(unreadable, dispatched, START_MARGIN)
    held_by = sorted([(p, "marked") for p in killed + denied] + [(p, "hidden") for p in unreadable]
                     + [(p, "cwd") for p in by_cwd[0] + by_cwd[1]]
                     + [(p, "worker-account") for p in held])  # fmt: skip
    killed, denied = killed + by_cwd[0], denied + by_cwd[1]
    # Linux /proc mounted with hidepid may hide any process, a same-uid one that is not
    # dumpable included: after killing what it shows, such a look is never "gone".
    hidden_by = _restricted()
    return Sweep(not hidden_by, len(killed), len(denied), len(unreadable), len(held),
                 tuple(held_by), hidden_by, vanished)  # fmt: skip


def _kill_marked(rows, marker: str) -> tuple[list[int], list[int], int]:
    """(pids SIGKILL was delivered to, pids that refused it, how many had already exited) of
    the rows that carry marker."""
    killed, denied, vanished = [], [], 0
    for pid, _pgid, _uid, environment in rows:
        if marker not in environment:
            continue
        try:
            os.kill(pid, signal.SIGKILL)
            killed.append(pid)
        except ProcessLookupError:
            vanished += 1  # exited after the listing: a child it started may be unlisted
        except PermissionError:
            denied.append(pid)  # still running (NIT #244: a refused kill is a survivor)
    return killed, denied, vanished


def confirm_gone(first: Sweep, again: Callable[[], Sweep]) -> Sweep:
    """Look again (``again``) after ``first`` until two looks in a row are gone, the table
    cannot be read, or CONFIRM_TRIES run out; return the last look. The second clean look,
    taken after every kill, shows that nothing the one before missed is left (e.g. a child of
    a marked process that exited before its kill). Clean only once at the end: ``unconfirmed``,
    so not gone."""
    found, clean = first, int(first.gone)
    for _ in range(CONFIRM_TRIES):
        if clean >= 2 or not found.listed:
            break
        time.sleep(CONFIRM_WAIT)  # SIGKILLed: wait until the kernel has ended them
        found = again()
        clean = clean + 1 if found.gone else 0
    return found if clean >= 2 or not found.gone else replace(found, unconfirmed=True)


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
    """Why /proc may hide processes; "" when it cannot (#244, proc_restrict)."""
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


def _kill_group(pgid: int) -> None:
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass  # the group is already gone
