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


def run_child(argv, payload, *, timeout, output_cap):
    """Run ``argv`` with ``payload`` on stdin; return ``(stdout, timed_out, returncode)``.

    On a timeout, output over ``output_cap``, a thread that cannot start, a spawn failure or a
    reader/writer that does not finish, the result is ``(b"", timed_out, 1)``: the caller refuses
    and never reads a partial result as success. The whole process group is killed and reaped on
    every exit path."""
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
        try:
            process.wait(timeout=timeout)
            timed_out = False
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_child_group(process.pid)
            process.wait()
        _kill_child_group(process.pid)
        writer.join(timeout=1)
        reader.join(timeout=1)
        if reader.is_alive() or writer.is_alive() or size >= output_cap:
            return b"", timed_out, 1
        return b"".join(output), timed_out, process.returncode
    except (OSError, RuntimeError):  # RuntimeError: a thread that cannot start
        return b"", False, 1
    finally:
        if process is not None:  # every exit path: kill the whole group, reap the child
            _kill_child_group(process.pid)
            process.wait()


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
