"""Parent side of the descriptor hand-over to the report-guard plugin (see report_guard.py).

``open_channel`` makes the two descriptors the child inherits through ``pass_fds``: a pipe that
already holds the nonce (write end closed, so the plugin reads to EOF) and an anonymous file the
plugin writes its record into. Neither has a name in the filesystem and neither number is secret
on its own; what the child learns from the environment is only ``CAIRNWORK_GUARD_FDS``, which
the plugin removes at import. The parent reads the record from its own descriptor.

Higher hurdle, not a boundary: code in the pytest process can still scan its open descriptors.

Raising the bar (this module, ``report_channel`` and ``tamper_scan``): when the verifier runs as
the owner (no verifier account) the nonce and the report destination no longer sit in the
environment. The parent hands the child two inherited file descriptors (``pass_fds``): a pipe
holding the nonce and an anonymous (already unlinked) file for the record; only their numbers
travel in ``CAIRNWORK_GUARD_FDS``. The plugin reads the nonce, closes that pipe and removes every
guard variable from ``os.environ`` when its module is imported, which is before any conftest or
test module (interpreter start-up hooks such as ``sitecustomize.py`` and ``.pth`` files run
earlier; they are protected verifier inputs a candidate cannot add). The parent reads the record
from its own descriptor, so there is no path to guess, replace or plant. Candidate patches are
also scanned for the APIs such a forgery needs (tamper_scan.py, reason ``VERIFIER_TAMPER_API``).
Both are a higher hurdle, not a boundary: a process that shares the interpreter can still scan its
open descriptors or build the API names from strings. Under a verifier account sudo closes
descriptors above 2, so that path keeps the environment hand-over (path and nonce); the plugin
removes it from ``os.environ`` once read, but the process's original environment stays readable
(``/proc/self/environ`` on Linux, ``KERN_PROCARGS2`` for the same uid on macOS), so on that path
the nonce is still within reach of the code under test.

Known gap: Seatbelt does not stop a sandboxed process from reading other
processes' arguments and environment (sysctl KERN_PROCARGS2) when they run as
the same user, so tokens in other processes' environments are readable and could
be printed into the verifier log. A VerifierAccount closes that (another uid);
any process the account still runs after a verifier is killed and fails the run,
and none may run before the next one. Without one, a process that leaves the
run's process group is killed only if it still carries the run token in its
environment. Either way, never pass raw verifier logs to a worker or a model.
"""

from __future__ import annotations

import os
import secrets
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from cairnwork import report_guard


@dataclass
class GuardChannel:
    nonce: str
    _report: IO[bytes]
    _nonce_read: int  # the parent's copy of the pipe's read end; -1 once closed

    @property
    def pass_fds(self) -> tuple[int, int]:
        return (self._nonce_read, self._report.fileno())

    def env(self) -> dict[str, str]:
        nonce_fd, report_fd = self.pass_fds
        return {report_guard.FDS_ENV: f"{nonce_fd},{report_fd}"}

    def read(self) -> report_guard.Verdict:
        return report_guard.read_verdict_fd(self._report.fileno(), self.nonce)

    def close(self) -> None:
        nonce_read, self._nonce_read = self._nonce_read, -1  # never retry a close
        try:
            if nonce_read >= 0:
                os.close(nonce_read)
        finally:  # #1364: a raising close above must not leave the report open
            self._report.close()


def open_channel(private: Path) -> GuardChannel:
    """A fresh channel with a new nonce; ``private`` holds the (immediately unlinked) report."""
    nonce = secrets.token_hex(16)
    read_end, write_end = os.pipe()
    try:
        os.write(write_end, nonce.encode())  # 32 bytes: far below the pipe buffer
    except BaseException:
        os.close(read_end)
        raise
    finally:
        os.close(write_end)
    try:
        report = tempfile.TemporaryFile(dir=private)  # noqa: SIM115 - closed by GuardChannel.close
    except BaseException:
        os.close(read_end)
        raise
    return GuardChannel(nonce, report, read_end)
