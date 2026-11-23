"""Run a helper process on untrusted input under a time limit and an output cap (#1256).

Moved out of ``p2p_collect`` unchanged in behaviour so every site that parses candidate-controlled
source (``ast.parse`` and friends) can run it in a child the parent can kill, instead of in-process.
The child gets an empty environment, its own session and stdin only; its stderr is dropped.
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time

_PS = "/bin/ps"
_POLL_SECONDS = 0.05


def run_child(argv, payload, *, timeout, output_cap, memory_limit=None):
    """Run ``argv`` with ``payload`` on stdin; return ``(stdout, timed_out, returncode)``.

    Output over ``output_cap``, a thread that cannot start, a spawn failure or a reader/writer
    that does not finish gives ``(b"", timed_out, 1)``: the caller refuses and never reads a
    partial result as success. A timeout alone (the cap not hit) returns the output read so far,
    ``True`` and the kill's return code (-9), so a caller must check ``timed_out`` first. Any
    other exit returns the child's own return code as is. The whole process group is killed and
    reaped on every exit path.

    ``memory_limit`` (bytes, #1246): the resident memory of the child's process group is polled
    while it runs; going over it, or a reading that cannot be taken, kills the group and gives
    ``(b"", False, 1)``, a refusal like the output cap. (``RLIMIT_AS`` is not enforced on macOS,
    so the parent measures.)"""
    output: list[bytes] = []
    size = 0
    process = None
    try:
        process = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env={},
            start_new_session=True,
        )
        stdout = process.stdout
        assert stdout is not None

        def drain():
            nonlocal size
            while chunk := stdout.read(8192):
                if size < output_cap:
                    kept = chunk[: output_cap - size]
                    output.append(kept)
                    size += len(kept)

        reader = threading.Thread(target=drain, daemon=True)
        writer = threading.Thread(target=_write_input, args=(process.stdin, payload), daemon=True)
        reader.start()
        writer.start()
        timed_out, over_memory = _wait(process, timeout, memory_limit)
        _kill_child_group(process.pid)
        process.wait()
        writer.join(timeout=1)
        reader.join(timeout=1)
        if reader.is_alive() or writer.is_alive() or size >= output_cap or over_memory:
            return b"", timed_out, 1
        return b"".join(output), timed_out, process.returncode
    except (OSError, RuntimeError):  # RuntimeError: a thread that cannot start
        return b"", False, 1
    finally:
        if process is not None:  # every exit path: kill the whole group, reap the child
            _kill_child_group(process.pid)
            process.wait()


def _wait(process, timeout, memory_limit):
    """Wait for the child; return ``(timed_out, over_memory)``. Both end with the group killed."""
    if memory_limit is None:
        try:
            process.wait(timeout=timeout)
            return False, False
        except subprocess.TimeoutExpired:
            _kill_child_group(process.pid)
            return True, False
    deadline = time.monotonic() + timeout
    while True:
        left = deadline - time.monotonic()
        if left <= 0:
            _kill_child_group(process.pid)
            return True, False
        try:
            process.wait(timeout=min(_POLL_SECONDS, left))
            return False, False
        except subprocess.TimeoutExpired:
            pass
        if _group_rss_bytes(process.pid) > memory_limit:
            _kill_child_group(process.pid)
            return False, True


def _group_rss_bytes(group):
    """Resident memory of every process in ``group``; a failed reading counts as over any limit."""
    try:
        done = subprocess.run(
            [_PS, "-axo", "pgid=,rss="],
            capture_output=True,
            env={},
            timeout=5,
            check=True,
            text=True,
        )
        total = 0
        for line in done.stdout.splitlines():
            fields = line.split()
            if len(fields) == 2 and int(fields[0]) == group:
                total += int(fields[1]) * 1024
        return total
    except (OSError, ValueError, subprocess.SubprocessError):
        return 1 << 62


def _write_input(stream, payload):
    try:
        stream.write(payload)
        stream.close()
    except (BrokenPipeError, OSError):
        pass


def _kill_child_group(group):
    try:
        os.killpg(group, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
