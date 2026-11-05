"""Deny channel for base runs (#797 step 1): record which sandbox denials the base run hit.

The verdict does not use it yet. A base run's failure may come from a sandbox denial that left no
trace in the exception (#801, #854). macOS reports each Seatbelt denial as a kernel log line, so
for a base run on macOS the runner:

1. adds ``(with message "<tag>")`` to every top-level ``(deny ...)`` form of the run's policy,
   with a new random tag per run. The kernel appends the tag to every deny line of the run's
   processes and their children, so a line is the run's exactly when it carries the tag;
2. streams ``log stream --style ndjson --predicate 'sender=="Sandbox"'`` to a file in a new 0700
   directory of the runner (neither the sandbox nor the verifier account can reach it);
3. runs an opening canary (a denied loopback connect under the same tagged policy), waits for its
   line, runs the base, runs a closing canary, waits for its line, then stops the stream.

The evidence is DENIED (a tagged line that is not on the benign list and not a canary's), CLEAN
(none, and the channel was healthy), or UNAVAILABLE with a reason. Loss is silent and host-wide:
another process's burst can drop the run's line without any marker, and a canary that arrived
does not show that nothing was lost. So the channel is UNAVAILABLE when the whole stream (every
sender's lines, ``N duplicate reports`` counted as N) went over ``MAX_WINDOW_LINES`` or
``MAX_LINES_PER_SECOND`` (step 0 O3, O3c). A denied ``access()`` or a missing path leaves no line
at all: CLEAN never means the base hit no denial.
"""

from __future__ import annotations

import os
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, replace
from functools import partial
from pathlib import Path

from cairnwork.deny_judge import (  # re-exported: the channel's callers and tests read them here
    BACKGROUND_PER_SECOND,
    BENIGN_DENIES,
    MAX_LINES_PER_SECOND,
    MAX_LISTED,
    MAX_WINDOW_LINES,
    TAG,
    TAG_PREFIX,
    UNSUPPORTED,
    Evidence,
    Window,
    judge,
    judge_stream,
)
from cairnwork_sandbox.backend import Policy

__all__ = [
    "BACKGROUND_PER_SECOND",
    "BENIGN_DENIES",
    "MAX_LINES_PER_SECOND",
    "MAX_LISTED",
    "MAX_WINDOW_LINES",
    "UNSUPPORTED",
    "Box",
    "Evidence",
    "Window",
    "judge",
    "recording",
    "tag_policy",
    "tagged",
    "watch",
]

LOG = "/usr/bin/log"
WAIT_SECONDS = 10.0  # for the header and for each canary's line
SETTLE_SECONDS = 1.0  # after the closing canary's line: lines still in flight
MAX_STREAM_BYTES = 32 << 20  # over it the stream is stopped: UNAVAILABLE (#859 review)
POLL_SECONDS = 0.2  # how often the size guard looks at the stream file
CANARY = (
    "import os, socket, sys\n"
    "print(os.getpid(), flush=True)\n"
    "s = socket.socket(); s.settimeout(2); s.connect_ex(('127.0.0.1', int(sys.argv[1])))\n"
)
SEATBELT = "seatbelt"


@dataclass
class Box:
    evidence: Evidence | None = None  # None: not recorded (not a base run)


def tagged(profile: Policy, backend: str, enabled: bool) -> tuple[Policy, str]:
    """The policy to run (tagged on Seatbelt when ``enabled``) and its tag ("" when untagged)."""
    if not enabled or backend != SEATBELT:
        return profile, ""
    tag = new_tag()
    try:
        return replace(profile, text=tag_policy(profile.text, tag)), tag
    except ValueError:  # recorded as UNAVAILABLE by watch(); the run itself goes ahead untagged
        return profile, ""


@contextmanager
def watch(enabled: bool, backend: str, tag: str, run_canary):
    """``recording`` for a tagged Seatbelt run; an UNAVAILABLE box elsewhere, nothing when off."""
    if not enabled:
        yield Box()
    elif backend != SEATBELT:
        yield Box(UNSUPPORTED)
    elif not tag:
        yield Box(Evidence("UNAVAILABLE", "the policy could not be tagged"))
    else:
        with recording(tag, run_canary) as box:
            yield box


def new_tag() -> str:
    return TAG_PREFIX + secrets.token_hex(8)


def tag_policy(text: str, tag: str) -> str:
    """``text`` with ``(with message "<tag>")`` closing every ``(deny ...)`` form (a nested one
    too; the generated policy has none). Strings (``"..."``, ``#"..."``) may hold parentheses.
    ValueError when there is no deny form or a form does not close."""
    if not TAG.fullmatch(tag):
        raise ValueError(f"bad tag {tag!r}")
    out, i, forms = [], 0, 0
    while i < len(text):
        if not text.startswith("(deny", i):
            out.append(text[i])
            i += 1
            continue
        end = _form_end(text, i)
        out.append(f'{text[i:end]} (with message "{tag}"))')
        forms, i = forms + 1, end + 1
    if not forms:
        raise ValueError("the policy has no deny form")
    return "".join(out)


def _form_end(text: str, start: int) -> int:
    depth, quoted, j = 0, False, start
    while j < len(text):
        char = text[j]
        if quoted:
            if char == "\\":
                j += 1
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return j
        j += 1
    raise ValueError("a deny form does not close")


@contextmanager
def recording(tag: str, run_canary: Callable[[list[str], Path], str]):
    """Stream the deny log around the ``with`` body; then ``box.evidence`` is set.
    ``run_canary(argv, log_path)`` runs argv under the run's tagged policy and returns its output.
    A recorder failure never stops the body: the evidence is UNAVAILABLE."""
    window, box = Window(tag), Box(UNSUPPORTED)
    made = _guarded(window, "no private directory", tempfile.mkdtemp, "", "cairnwork-deny.")
    if made is None:
        yield Box(Evidence("UNAVAILABLE", window.problem))
        return
    directory = Path(made)
    path, proc, guard = directory / "stream.ndjson", None, None
    try:
        proc = _guarded(window, "log stream did not start", _start, path, window)
        if proc is not None:
            guard = _guarded(window, "the size guard did not start", _SizeGuard, path, proc, window)
            if guard is None:  # nothing would bound the file: stop the stream now
                _halt(None, proc, window)
        if not window.problem:
            _guarded(window, "a canary failed", _canary, run_canary, directory, window, path, 2)
        yield box
        if not window.problem:
            _guarded(window, "a canary failed", _canary, run_canary, directory, window, path)
            time.sleep(SETTLE_SECONDS)
            if proc is not None and proc.poll() is not None:
                window.problem = window.problem or "log stream exited during the run"
        _halt(guard, proc, window)  # stopped before the read: no line is still being written
        evidence = _guarded(window, "the stream could not be judged", _judge_file, path, window)
        box.evidence = evidence or Evidence("UNAVAILABLE", window.problem)
    finally:
        _halt(guard, proc, window)
        shutil.rmtree(directory, ignore_errors=True)


def _guarded(window: Window, problem: str, step, *args):
    """``step(*args)``; an Exception it raises becomes the window's problem, never the run's."""
    try:
        return step(*args)
    except Exception as exc:  # noqa: BLE001 - recording must not change what the base run does
        window.problem = window.problem or f"{problem}: {type(exc).__name__}"
        return None


class _SizeGuard(threading.Thread):
    """Stops the stream once its file passes MAX_STREAM_BYTES: many distinct denials (from any
    process on the host) must not fill the disk or the runner's memory."""

    def __init__(self, path: Path, proc, window: Window) -> None:
        super().__init__(daemon=True)
        self.path, self.proc, self.window, self.done = path, proc, window, threading.Event()
        self.start()

    def run(self) -> None:
        while not self.done.wait(POLL_SECONDS):
            if problem := _over(self.path):
                self.window.problem = self.window.problem or problem
                _guarded(self.window, "the stream did not stop", _stop, self.proc)
                return


def _over(path: Path) -> str:
    """Why the stream must stop now ("" when it may go on). A file that cannot be looked at is
    over: the stream may be writing to an unlinked file nothing bounds."""
    try:
        size = os.stat(path).st_size
    except OSError as exc:
        return f"the stream file cannot be looked at: {type(exc).__name__}"
    return "the stream went over its byte cap: lines may be lost" if size > MAX_STREAM_BYTES else ""


def _halt(guard: _SizeGuard | None, proc, window: Window) -> None:
    """Stop the guard, then the stream; never raises (a failure is the window's problem)."""
    if guard is not None:
        guard.done.set()
        _guarded(window, "the size guard did not stop", guard.join, 5)
    _guarded(window, "the stream did not stop", _stop, proc)


def _stop(proc) -> None:
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def _start(path: Path, window: Window):
    if not os.access(LOG, os.X_OK):
        window.problem = "log is missing"
        return None
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    window.inode = os.fstat(fd).st_ino
    try:
        argv = [LOG, "stream", "--style", "ndjson", "--predicate", 'sender=="Sandbox"']
        proc = subprocess.Popen(
            argv, stdin=subprocess.DEVNULL, stdout=fd, stderr=subprocess.DEVNULL
        )
    finally:
        os.close(fd)
    if not _wait(lambda: _found(path, b"\n", window), proc):
        window.problem = "no stream header"
    return proc


def _canary(run_canary, directory: Path, window: Window, path: Path, tries: int = 1) -> None:
    """Run a canary and wait for its line; ``tries`` > 1 (the opening one) runs a new canary when a
    line does not come: a stream that just started may miss its first lines (step 0 O4)."""
    for _ in range(tries):
        port = 49152 + secrets.randbelow(16000)
        log = directory / f"canary{len(window.canaries) + len(window.retired)}.log"
        first = (run_canary([CANARY, str(port)], log).split() or [""])[0]
        if not first.isdigit():
            window.problem = window.problem or "a canary did not start"
            return
        key, wait = (int(first), f"remote:*:{port}"), WAIT_SECONDS / tries
        needle = f"({key[0]}) deny(1) network-outbound {key[1]}\\n{window.tag}".encode()
        if _wait(partial(_found, path, needle, window), None, wait):
            window.canaries.append(key)
            return
        window.retired.append(key)
    window.problem = window.problem or "a canary line is missing"


def _found(path: Path, needle: bytes, window: Window) -> bool:
    """Whether ``needle`` is in the stream file after the last search (overlapping by the
    needle's length); reads only what was added since, at most MAX_STREAM_BYTES."""
    with path.open("rb") as handle:
        handle.seek(max(0, window.offset - len(needle)))
        added = handle.read(MAX_STREAM_BYTES)
        window.offset = handle.tell()
    return needle in added


def _wait(ready: Callable[[], bool], proc, seconds: float = WAIT_SECONDS) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if ready():
            return True
        if proc is not None and proc.poll() is not None:
            return False
        time.sleep(0.05)
    return False


def _judge_file(path: Path, window: Window) -> Evidence:
    if os.lstat(path).st_ino != window.inode:
        window.problem = window.problem or "the stream file was replaced"
    with path.open("rb") as handle:
        return judge_stream(handle, window)
