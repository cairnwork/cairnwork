"""Worker subprocess launch that cannot leave descendants behind."""

from __future__ import annotations

import os
import signal
import subprocess
import tempfile


def spawn_worker(argv, cwd):
    """Run the worker in its own session, then SIGKILL its whole process group so no descendant
    outlives the episode and touches the checkout while Core judges it (best effort: processes the
    relay may not signal are caught by Core's own tree-digest recheck)."""
    with tempfile.TemporaryFile("w+") as out_file, tempfile.TemporaryFile("w+") as err_file:
        proc = subprocess.Popen(
            argv, cwd=cwd, stdout=out_file, stderr=err_file, text=True, start_new_session=True
        )
        try:
            proc.wait()
        finally:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        out_file.seek(0)
        err_file.seek(0)
        stdout, stderr = out_file.read(), err_file.read()
    return subprocess.CompletedProcess(argv, proc.returncode, stdout, stderr)
