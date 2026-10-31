"""AC8's controller (#476 step 3b): the read-only guard before the plan, and the one event log.

AC8 (a): until the plan is delivered the worker's tests run only through local_worker's
``pytest-ro``, whose Seatbelt profile denies every write in the checkout. Seatbelt holds for the
test process and everything it starts, for as long as they live, so a process a call leaves
behind cannot write the checkout either, not even after a later writable call. (b), a digest
before and after every call with rollback, was tried first and dropped: a process that local
worker's sweep does not find (one that clears its environment and leaves the run root) can write
after any digest, and no digest can be atomic with it (two outside review rounds).

``PrePlanGuard.start`` must come before the run's first tool call, and enforces it: a tools
object that has started any wrapper call is refused (GuardUnavailable), since an earlier writable
call could have left a process or an open descriptor that can still write. It then puts the tools
in their read-only window (``read_only_until_plan``: write and the writable pytest are refused
before they start, never redirected), on macOS only (local_worker's sandbox is Seatbelt, so
elsewhere GuardUnavailable), probes ``pytest-ro --version`` (an installed wrapper without pytest-ro
refuses the unknown action: GuardUnavailable, never the writable pytest) and takes the checkout
digest. ``run_tests`` runs pytest-ro only. ``at_plan_arrival`` digests again; any difference
stops the run (GuardUnavailable) with nothing repaired, and only a match, once its event is
recorded, ends the window.

``JsonlEventLog`` is the one controller of the events: one JSON line per event, numbered 1, 2,
3, ... under one lock, in a file it creates exclusively inside a directory it has checked is its
own, mode 0700 and outside the checkout (the file is opened through that checked directory)."""

from __future__ import annotations

import json
import os
import stat
import sys
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from experimental.pause.guard import outside
from experimental.pause.interfaces import CheckoutGuard, EventLog, GuardUnavailable

if TYPE_CHECKING:
    from experimental.local_worker.toolcall import ToolCall
    from experimental.local_worker.tools import WorkerTools

PROBE_ARGS = ("--version",)
REFUSED = b"local-worker-tool: refusing:"  # the wrapper's refusal (exit 64): nothing ran


class PrePlanGuard:
    """See the module docstring; GuardUnavailable is never swallowed: the run falls back to the
    bare run."""

    def __init__(self, guard: CheckoutGuard, events: EventLog, tools: WorkerTools) -> None:
        self.guard, self.events, self.tools = guard, events, tools
        self.baseline: str | None = None
        self.delivered = False

    def start(self) -> None:
        if sys.platform != "darwin":
            raise GuardUnavailable(
                f"local_worker's sandbox is macOS Seatbelt; none on {sys.platform}"
            )
        # A call before the guard may have left a writable process or fd; from here on write
        # and the writable pytest are refused until the plan arrives (one step, under one lock).
        if not self.tools.enter_read_only_if_unused():
            raise GuardUnavailable("the guard must start before the run's first tool call")
        probe = self.tools.run_tests(list(PROBE_ARGS), read_only=True)
        if probe.exit_code != 0:
            raise GuardUnavailable(f"the worker's wrapper did not run pytest-ro: {probe.output!r}")
        self.baseline = self.guard.digest()
        self.events.record("guard_start", digest=self.baseline)

    def run_tests(self, pytest_args: Sequence[str]) -> ToolCall:
        if self.baseline is None or self.delivered:
            raise GuardUnavailable("run_tests outside the guarded window")
        self.events.record("tool_before", tool="run_tests")
        call = self.tools.run_tests(pytest_args, read_only=True)
        self.events.record("tool_after", tool="run_tests", exit_code=call.exit_code)
        if call.exit_code == 64 and REFUSED in call.raw:
            raise GuardUnavailable("the worker's wrapper refused pytest-ro")
        return call

    def at_plan_arrival(self) -> None:
        if self.baseline is None or self.delivered:
            raise GuardUnavailable("the plan arrived outside the guarded window")
        now = self.guard.digest()
        if now != self.baseline:
            self.events.record("guard_changed", before=self.baseline, after=now)
            raise GuardUnavailable("the checkout changed before the plan although it was read-only")
        self.events.record("plan_arrival", digest=now)  # raises: the window stays read-only
        self.delivered = True
        self.tools.read_only_until_plan = False


class JsonlEventLog:
    """See the module docstring; a write that fails stops the run, and its number is not used."""

    def __init__(self, path: Path, *, checkout: Path) -> None:
        parent, name = os.path.split(os.path.abspath(path))
        try:
            pfd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except OSError as e:
            raise GuardUnavailable(f"event log directory: {e.strerror}") from e
        try:
            st = os.fstat(pfd)
            if st.st_uid != os.geteuid() or stat.S_IMODE(st.st_mode) != 0o700:
                raise GuardUnavailable("the event log's directory must be the controller's, 0700")
            if not outside(parent, checkout):
                raise GuardUnavailable("the event log must be outside the checkout")
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_APPEND
            self._fd = os.open(name, flags, 0o600, dir_fd=pfd)
        except OSError as e:
            raise GuardUnavailable(f"event log: {e.strerror}") from e
        finally:
            os.close(pfd)
        self._seq, self._lock = 0, threading.Lock()

    def record(self, kind: str, **fields: Any) -> int:
        if "seq" in fields:
            raise ValueError("seq is the controller's to assign")
        with self._lock:
            seq = self._seq + 1
            data = (
                json.dumps({**fields, "kind": kind, "seq": seq}, sort_keys=True) + "\n"
            ).encode()
            try:
                written = os.write(self._fd, data)
            except OSError as e:
                raise GuardUnavailable(f"event log: {e.strerror}") from e
            if written != len(data):
                raise GuardUnavailable("event log: short write")
            self._seq = seq
            return seq

    def close(self) -> None:
        os.close(self._fd)


def read_events(path: Path) -> list[dict[str, Any]]:
    """The log's events; ValueError unless their numbers are exactly 1, 2, 3, ... in order."""
    events = [json.loads(line) for line in Path(path).read_text().splitlines()]
    if [e.get("seq") for e in events] != list(range(1, len(events) + 1)):
        raise ValueError("event sequence numbers are not 1..n in order")
    return events
