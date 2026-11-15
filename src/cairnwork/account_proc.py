"""Run one account-cleanup command with a hard time bound (#437 H)."""

from __future__ import annotations

import subprocess
from contextlib import suppress

TERM_GRACE_SECONDS = 5


def run_bounded(argv: list[str], timeout: float, fail: type[Exception]) -> None:
    """Run ``argv`` (output dropped). On timeout end only this child, then raise ``fail``.

    ``subprocess.run`` kills with SIGKILL, which sudo cannot pass on, so the command it started as
    the account kept running. SIGTERM goes to our own child's pid only (no pkill, no process
    group); sudo relays it. A timeout is never reported as success.
    """
    proc = subprocess.Popen(  # noqa: S603 - fixed argv, path checked by the caller
        argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        with suppress(OSError):
            proc.terminate()
        try:
            proc.wait(timeout=TERM_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            with suppress(OSError):
                proc.kill()
            proc.wait()
        raise fail(f"account cleanup timed out after {timeout:g}s") from None
    except BaseException:
        with suppress(OSError):
            proc.kill()
        proc.wait()
        raise
