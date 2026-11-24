"""A ``subprocess.run`` stand-in for git that stops reading stdout past a byte cap (#766).

``run_git_bytes(max_bytes=N)`` uses it, so one oversized read is refused before it fills memory.
"""

from __future__ import annotations

import subprocess
import threading
from collections.abc import Callable, Sequence
from functools import partial
from typing import Any

STDERR_READ_LIMIT = 1 << 16  # stderr kept for an error text; the rest is drained and dropped


class GitOutputTooLarge(Exception):
    """git's stdout went past ``max_bytes``; git was killed and nothing is returned."""


def run_capped(  # noqa: PLR0913 - the keywords subprocess.run takes, as the call in gitcmd._exec
    argv: Sequence[str], *, max_bytes: int, input: bytes | None, timeout: float,  # noqa: A002
    env: dict[str, str], **_ignored: Any,
) -> subprocess.CompletedProcess[bytes]:  # fmt: skip
    """subprocess.run(check=True, capture_output=True) for bytes, capped at max_bytes of stdout."""
    proc = subprocess.Popen(  # noqa: S603 - fixed git argv, no shell
        argv, stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
    )  # fmt: skip
    assert proc.stdout is not None and proc.stderr is not None
    errors: list[bytes] = []
    chunks: list[bytes] = []
    size = 0

    def drain_stderr() -> None:  # a full stderr pipe must not stall git
        errors.append(proc.stderr.read(STDERR_READ_LIMIT))  # type: ignore[union-attr]
        while proc.stderr.read(65536):  # type: ignore[union-attr]
            pass

    def feed() -> None:
        try:
            proc.stdin.write(input)  # type: ignore[union-attr]
            proc.stdin.close()  # type: ignore[union-attr]
        except OSError:  # git stopped reading
            pass

    expired = threading.Event()

    def kill_on_timeout() -> None:
        expired.set()
        proc.kill()

    helpers = [threading.Thread(target=drain_stderr, daemon=True)]
    if input is not None:
        helpers.append(threading.Thread(target=feed, daemon=True))
    for thread in helpers:
        thread.start()
    timer = threading.Timer(timeout, kill_on_timeout)
    timer.start()
    try:
        while block := proc.stdout.read1(65536):  # type: ignore[attr-defined]
            size += len(block)
            if size > max_bytes:
                raise GitOutputTooLarge(f"git output is larger than {max_bytes} bytes")
            chunks.append(block)
        proc.wait()
        for thread in helpers:
            thread.join(1)
    finally:
        timer.cancel()
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        proc.stdout.close()
        proc.stderr.close()
    if expired.is_set():
        raise subprocess.TimeoutExpired(argv, timeout)
    out, err = b"".join(chunks), b"".join(errors)
    if proc.returncode:
        raise subprocess.CalledProcessError(proc.returncode, argv, out, err)
    return subprocess.CompletedProcess(argv, 0, out, err)


def capped(max_bytes: int) -> Callable[..., subprocess.CompletedProcess[bytes]]:
    """``run_git_bytes(max_bytes=N)``: bounds what is read (#766); past it git is killed and
    GitOutputTooLarge is raised."""
    return partial(run_capped, max_bytes=max_bytes)
