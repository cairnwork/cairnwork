"""A worker that asks an Agy (Antigravity CLI) model to edit the attempt clone.

The model runs in print mode with ``--mode accept-edits --sandbox`` and the
clone as its working directory. What that actually allows was probed on
2026-10-20 (docs/DRILLS.md); it is not a security boundary. Cairnwork never
trusts the worker: it snapshots only the allowed files and rejects any other
change in the clone.
The agy process runs in its own process group, which is killed afterwards. It
gets only the environment it needs: it signs in from files under HOME (checked
2026-10-20), plus locale, config dirs, CA bundles and proxies. Other variables in
Cairnwork's environment do not reach it; proxy URLs may carry credentials, and
agy can read anything under HOME, so this narrows exposure, it is no boundary.
Its output is copied into the log by the parent, capped like a verifier log; a
process it started outside its group is killed if it still carries the attempt
marker, and one that still holds the output afterwards fails the run, fenced (#537). If that
sweep cannot show the marked processes gone (a kill refused, the table unreadable), the run raises
WorkerMayLive, so the attempt is fenced like an unswept worker account's (#244, #63): on the
owner's account, or with ``agy_run_as_user`` on the worker account (its takeover sweep covers
the owner's marked processes too).
With an account-pool dispatcher configured (agy_dispatch.py) the worker runs that
instead of agy, prompt on stdin, same environment plus the pool's tuning variables.
With ``agy_run_as_user`` set as well, the dispatcher runs agy as that worker account,
and the attempt succeeds only if the dispatcher's worker receipts prove it ran as that
account and was swept clean; a run left behind by a killed dispatcher is swept through
the wrapper (agy_receipts.py).
"""

from __future__ import annotations

import os
import pwd
import shutil
import signal
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

from cairnwork import proc_start, provenance
from cairnwork.confined import ATTEMPT_ENV, Sweep, confirm_gone, copy_capped, sweep_marked
from cairnwork.unswept import WorkerMayLive
from cairnwork.workers import agy_dispatch, agy_receipts

ENV_KEEP = (
    "HOME",
    "PATH",
    "USER",
    "LOGNAME",
    "SHELL",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TMPDIR",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "XDG_CACHE_HOME",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "NODE_EXTRA_CA_CERTS",
    "REQUESTS_CA_BUNDLE",
    "ALL_PROXY",
    "all_proxy",
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "NO_PROXY",
    "https_proxy",
    "http_proxy",
    "no_proxy",
)

PROMPT = """You are fixing a small bug in this repository.

Task: {title}

{instructions}

Rules:
- Edit only these files: {allowed}
- Do not create, delete or rename any other file. Do not run commands.
- Make the smallest change that fixes the task; keep the existing style.
- Do not modify tests.
- Work alone and edit the allowed files yourself in this turn: do not start subagents,
  researchers or background tasks (in this mode they never report back).
{hidden_tests}"""

# Only true for a task with deferred tests (test_commit): otherwise the judging tests are
# the repository's own, which the worker may and should read.
HIDDEN_TESTS = """- The tests that will judge your fix are not in this repository; do not look for
  them. Read the source you need directly.
"""


Attempt = tuple[str, str, tuple[str, ...]]  # (attempt name, boot mark, run dirs for the sweep)


class AgyWorkerError(Exception):
    pass


class AgyWorkerUnswept(AgyWorkerError, WorkerMayLive):
    """WORKER_SWEEP_UNCLEAN: worker-account processes may survive; the attempt is fenced (#244)."""


def _run_env() -> dict[str, str]:
    """What the dispatcher is given (the --status lookup gets the same, so it sees the same
    operation store): the kept keys plus the pool's own NEXUS_AGY_* tuning."""
    return _kept_env() | agy_dispatch.tuning_env()


def _kept_env() -> dict[str, str]:
    return {key: os.environ[key] for key in ENV_KEEP if key in os.environ}


def _uid(user: str) -> int | None:
    try:
        return pwd.getpwnam(user).pw_uid
    except KeyError:
        return None


@dataclass(frozen=True)
class AgyWorker:
    model: str = "gemini-3.8-flash-high"
    timeout_seconds: int = 900
    agy: str = "agy"
    dispatch: agy_dispatch.Dispatch | None = None
    verifier_user: str | None = None  # the verifier account, if any (isolation, #234)

    @property
    def run_as(self) -> str:
        """The worker account agy runs as, "" for the owner's uid (journaled, #244)."""
        return (self.dispatch.run_as_user or "") if self.dispatch is not None else ""

    @property
    def isolated(self) -> bool:
        """Runs as another uid (agy_run_as_user): pipeline may then report UNCOMPARABLE (#234)."""
        return not self.not_isolated_because

    @property
    def not_isolated_because(self) -> str:
        """ "" only when agy runs as a uid that is neither root, the owner (os.getuid()) nor the
        verifier account: a worker sharing one of them could break the base runs or the account."""
        user = self.dispatch.run_as_user if self.dispatch is not None else None
        if user is None:
            return "agy_run_as_user is not set"
        uid = _uid(user)
        if uid is None:
            return f"agy_run_as_user {user} does not exist"
        if uid in (0, os.getuid()):
            return f"agy_run_as_user {user} is uid {uid}: root or the owner"
        if self.verifier_user is not None:  # None: no verifier account configured
            verifier = _uid(self.verifier_user)
            if verifier is None:
                return f"verifier account {self.verifier_user} unresolved"
            if uid == verifier:
                return f"agy_run_as_user {user} is the verifier account (uid {uid})"
        return ""

    def run(self, workdir: Path, task) -> dict[str, str]:
        dispatch = self.dispatch
        binary = shutil.which(self.agy) if dispatch is None else None
        if dispatch is None:
            if binary is None:
                raise AgyWorkerError(f"{self.agy} not found")
        elif not (os.path.isfile(dispatch.path) and os.access(dispatch.path, os.X_OK)):
            raise AgyWorkerError(f"{dispatch.path} not found or not executable")
        prompt = PROMPT.format(
            title=task.title,
            instructions=task.instructions,
            allowed=", ".join(sorted(task.allowed_files)),
            hidden_tests=HIDDEN_TESTS if getattr(task, "test_commit", "") else "",
        )
        if dispatch is not None:
            argv = agy_dispatch.argv(
                dispatch, workdir, self.model, self.timeout_seconds, task.allowed_files
            )
        else:
            argv = [
                binary,
                "-p",
                prompt,
                "--model",
                self.model,
                "--mode",
                "accept-edits",
                "--sandbox",
                "--print-timeout",
                f"{self.timeout_seconds}s",
            ]
        log_path = workdir.parent / f"{workdir.name}.agy.log"
        fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        env = _kept_env() if dispatch is None else _run_env()
        env[ATTEMPT_ENV] = workdir.name
        log = os.fdopen(fd, "wb")
        mark = proc_start.boot_mark()  # before the start: what predates it is not the run's
        dirs = (os.path.realpath(workdir),)  # now: the run may swap it (#578, #537)
        process = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
            argv,
            cwd=workdir,
            env=env,
            stdin=subprocess.DEVNULL if dispatch is None else subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT if dispatch is None else subprocess.PIPE,
            start_new_session=True,
        )
        reader = None
        if dispatch is not None:
            reader = agy_dispatch.StatusReader(process.stderr)
            reader.start()
            threading.Thread(target=_feed, args=(process, prompt), daemon=True).start()
        copier = threading.Thread(target=copy_capped, args=(process.stdout, log), daemon=True)
        copier.start()
        self._await(process, copier, reader, (workdir.name, mark, dirs), log, log_path)
        return self.provenance(reader)

    def provenance(self, reader) -> dict[str, str]:
        """Worker and requested model are Cairnwork's own config. The observed model is only
        what the dispatcher's own record (``--status``) says about the operation named by the
        launch receipt of the attempt that produced the candidate: the last attempt that has
        a launch receipt. Never the stderr status line. Only with ``agy_run_as_user`` (the
        worker is then another uid and cannot forge the receipts or the record), and only
        for a COMPLETED operation. Else unknown."""
        dispatch, observed = self.dispatch, None
        if reader is not None and dispatch is not None and dispatch.run_as_user is not None:
            found = agy_receipts.parse(reader.lines(), dispatch.run_as_user).attempts.values()
            launches = [
                a.receipts[agy_receipts.LAUNCH] for a in found if agy_receipts.LAUNCH in a.receipts
            ]
            if launches:
                op = launches[-1].get("operation_id")
                observed = agy_dispatch.operation_model(dispatch, op, _run_env())
        return provenance.make("agy", self.model, observed)

    def _await(self, process, copier, reader, attempt: Attempt, log, log_path) -> None:
        dispatch = self.dispatch
        timed_out = None
        try:
            process.wait(timeout=self.timeout_seconds + 60)
        except subprocess.TimeoutExpired as exc:
            timed_out = exc
            if dispatch is not None:
                agy_dispatch.ask_to_stop(process)  # lets it release its lease and sweep itself
        finally:
            name, mark, dirs = attempt
            held, swept, by_cwd = _stop(process, copier, name, reader, mark, dirs)
            if not held:  # else the copier still reads: leave both open rather than hang
                process.stdout.close()
                if process.stderr is not None:
                    process.stderr.close()
                log.close()
        completion, why = None, "HELD" * held or "CWD" * bool(by_cwd)  # cwd: as a holder (#537)
        try:
            if dispatch is not None and dispatch.run_as_user is not None:
                completion = _settle_worker_run(dispatch, reader, process, why, timed_out, log_path)
        except Exception as exc:  # an interrupt is not replaced: recovery then sweeps the run
            self._fence(swept, log_path, exc)
            if process.returncode < 0 and not isinstance(exc, WorkerMayLive):  # #537 B
                raise AgyWorkerUnswept(  # a signal end stays fenced, whatever settling raised
                    f"WORKER_SWEEP_UNCLEAN: KILLED_BY_SIGNAL: {-process.returncode}; settling"
                    f" the run raised {type(exc).__name__}; log {log_path}",
                    self.run_as,
                ) from exc
            raise
        self._fence(swept, log_path)
        if process.returncode < 0:  # any signal end, ours included (#244); with agy_run_as_user
            raise AgyWorkerUnswept(  # _settle_worker_run raised already
                "WORKER_SWEEP_UNCLEAN: "
                + "agy timed out; " * (timed_out is not None)
                + f"KILLED_BY_SIGNAL: {-process.returncode}; launches after the last receipt "
                f"unknown; log {log_path}",
                self.run_as,
            ) from timed_out
        # a holder without the marker is not one the sweep finds (#537 A); one found only by its
        # working directory in the clone is the same kind of leftover, killed or not: fenced too
        if held or by_cwd:
            raise AgyWorkerUnswept(
                "WORKER_SWEEP_UNCLEAN: "
                + "agy timed out; " * (timed_out is not None)
                + "HELD: a process agy started outlived it and holds its output; " * held
                + f"CWD: {by_cwd} process(es) in the clone tied to the attempt; " * bool(by_cwd)
                + f"log {log_path}",
                self.run_as,
            ) from timed_out
        if timed_out is not None:
            raise AgyWorkerError("agy timed out") from timed_out
        if reader is not None:
            agy_dispatch.raise_if_exhausted(reader.status(), process.returncode, log_path)
            agy_dispatch.raise_if_worker_refused(reader.status(), log_path)
        if process.returncode != 0:
            why = f"; {reader.describe()}" if reader is not None else ""
            raise AgyWorkerError(f"agy exited {process.returncode}{why}; log {log_path}")
        if completion is not None:
            raise agy_dispatch.AgyWorkerAccountFailed(f"{completion}; log {log_path}")

    def _fence(self, swept: Sweep, log_path, cause: Exception | None = None) -> None:
        """The owner-side sweep did not show the run gone: fenced, whatever else ended it, a
        timeout included (#63 (a)). On the run's account: with agy_run_as_user the takeover sweep
        of the worker account also kills the owner's marked processes (confined.sweep_marked)."""
        if not swept.gone:
            kept = swept.kept_by()
            raise AgyWorkerUnswept(
                f"WORKER_SWEEP_UNCLEAN: {swept.problem()}"
                + f"; kept by: {kept}" * bool(kept)
                + f"; log {log_path}",
                self.run_as,
            ) from cause


def _settle_worker_run(dispatch, reader, process, held, timed_out, log_path) -> str | None:
    """Judge a run under agy_run_as_user from the dispatcher's receipts and write its
    record. However the dispatcher ended, every launched run whose receipts do not prove
    a clean sweep and removal is swept through the wrapper first (a removed root after a
    clean sweep receipt needs nothing). Raises for an attempt the receipts do not show
    isolated and clean and for an unswept run, fenced (AgyWorkerUnswept) whenever a launch may
    outlive it: unusable receipts, no final status line, or a completed attempt with no launch
    receipt; returns the
    completion problem of an exit-0 run (raised after the exit-code checks, so a
    dispatcher-reported failure keeps its own label)."""
    known = not (held or reader.unknown or reader.dropped)
    receipts = agy_receipts.parse(reader.lines(), dispatch.run_as_user, known=known)
    code = process.returncode
    killed = timed_out is not None or code < 0
    unswept = agy_receipts.sweep_after_kill(dispatch, receipts, killed=killed)
    found = agy_receipts.problems(receipts)
    completion = agy_receipts.completion_problem(receipts) if code == 0 else None
    signalled = code < 0  # ours or not (#244): a later launch may be unrecorded
    # no final status line: it ended outside its protocol (a crash, os._exit), so a launch
    # after the last receipt may be unrecorded too, whatever the exit code (#546)
    unfinished = receipts.final is None and not signalled
    ended = ["TIMED_OUT"] * (timed_out is not None) + [held] * bool(held)  # "HELD" or "CWD"
    ended += [f"KILLED_BY_SIGNAL: {-code}; launches after the last receipt unknown"] * signalled
    ended += ["NO_FINAL_STATUS: launches after the last receipt unknown"] * unfinished
    extra = [completion] if completion else []
    # unswept: a launch not shown gone, or receipts that cannot show it, however the dispatcher
    # ended (#244, #546); so is a completed attempt with no launch receipt (#546)
    unproven = (completion or "").startswith("WORKER_RECEIPT_MISSING")
    may_live = unswept or signalled or unfinished or unproven
    try:  # decided before the record write: a failing write cannot drop the fence (#244)
        agy_receipts.write_record(log_path, receipts, unswept + ended + found + extra)
    finally:
        if may_live:
            raise AgyWorkerUnswept(  # noqa: B012 - the fence outranks a failed record write
                f"WORKER_SWEEP_UNCLEAN: worker-account processes may survive: {unswept + ended}; "
                f"receipt problems: {found + extra}; log {log_path}",
                dispatch.run_as_user,
            )
    if found and not ended:
        raise agy_dispatch.AgyWorkerAccountFailed(f"{'; '.join(found + unswept)}; log {log_path}")
    return completion


def _feed(process, prompt: str) -> None:
    try:
        process.stdin.write(prompt.encode())
    except OSError:
        pass  # the dispatcher exited without reading it; its exit code says what happened
    finally:
        try:
            process.stdin.close()
        except OSError:
            pass


def _stop(
    process, copier, attempt: str, reader=None, mark: str = "", dirs: tuple[str, ...] = ()
) -> tuple[bool, Sweep, int]:
    """Kill agy's group and anything it started outside it that still carries the attempt
    marker; return three values: whether something still holds agy's output pipe, the sweep,
    and how many processes it tied to the clone by cwd (below). The sweep is gone only when two
    looks in a row after the kill find none of the owner's processes marked (confirm_gone, as
    takeover.py): else the run may live and is fenced (#63 (a)). ``mark``: the
    boot mark taken before agy started; a hidden process older than it is not the run's, as in
    takeover.py (Linux; "" elsewhere, where none is hidden for good). ``dirs``: the clone's
    realpath taken before agy started; on macOS a process whose environment is withheld is tied
    to the attempt when its working directory is inside it, as in confined.execute (#537); those
    count once each, at any look (the run is then fenced, even once they are gone)."""
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass  # the group is already gone
    process.wait()

    by_cwd: set[int] = set()

    def look() -> Sweep:  # each process tied by its cwd counts once, as confined._sweep_survivors
        one = sweep_marked(
            ATTEMPT_ENV, attempt, skip_group=process.pid, dispatched=mark, cwd_dirs=dirs
        )
        by_cwd.update(pid for pid, why in one.held_by if why == "cwd")
        return one

    swept = confirm_gone(look(), look)
    copier.join(timeout=10)
    if reader is not None:
        reader.join(timeout=10)
    return copier.is_alive() or (reader is not None and reader.is_alive()), swept, len(by_cwd)
