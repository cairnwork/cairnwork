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
GAUGE_FAILED = 1000  # return code: the memory gauge could not be read (not a real exit status)


def run_child(argv, payload, *, timeout, output_cap, memory_limit=None):
    """Run ``argv`` with ``payload`` on stdin; return ``(stdout, timed_out, returncode)``.

    Output over ``output_cap``, a thread that cannot start, a spawn failure or a reader/writer
    that does not finish gives ``(b"", timed_out, 1)``: the caller refuses and never reads a
    partial result as success. A timeout alone (the cap not hit) returns the output read so far,
    ``True`` and the kill's return code (-9), so a caller must check ``timed_out`` first. Any
    other exit returns the child's own return code as is. The whole process group is killed and
    reaped on every exit path.

    ``memory_limit`` (bytes, #1246) is a sampled soft limit: the resident memory of the child's
    process group is read every 50 ms while it runs, so a spike shorter than a sample can pass,
    and a process that leaves the group (its own session) is not counted. Going over the limit
    kills the group and gives ``(b"", False, 1)``; a reading that cannot be taken while the group
    is still alive kills it too and gives ``(b"", False, GAUGE_FAILED)`` (a host problem, not the
    child's, #1700). Both are refusals like the output cap. A reading that runs out of the time
    limit counts as a timeout. (``RLIMIT_AS`` is not enforced on macOS, so the parent measures.)"""
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
        timed_out, over_memory, gauge_failed = _wait(process, timeout, memory_limit)
        _kill_child_group(process.pid)
        process.wait()
        writer.join(timeout=1)
        reader.join(timeout=1)
        if gauge_failed:
            return b"", False, GAUGE_FAILED
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
    """Wait for the child; return ``(timed_out, over_memory, gauge_failed)``.

    Each of the three ends with the group killed."""
    if memory_limit is None:
        try:
            process.wait(timeout=timeout)
            return False, False, False
        except subprocess.TimeoutExpired:
            _kill_child_group(process.pid)
            return True, False, False
    deadline = time.monotonic() + timeout
    while True:
        left = deadline - time.monotonic()
        if left <= 0:
            _kill_child_group(process.pid)
            return True, False, False
        try:
            process.wait(timeout=min(_POLL_SECONDS, left))
            return False, False, False
        except subprocess.TimeoutExpired:
            pass
        used = _group_rss_bytes(process.pid, max(deadline - time.monotonic(), 0.01))
        if used is None or used > memory_limit:
            _kill_child_group(process.pid)
            ran_out = used is None and time.monotonic() >= deadline
            return ran_out, used is not None, used is None and not ran_out


def _group_rss_bytes(group, budget):
    """Resident bytes of every process in ``group``, or None when it cannot be read.

    The reading gets at most ``budget`` seconds (the time the child has left). No row for the
    group is an exited group (0) only when ``killpg(group, 0)`` says it is gone; a group that is
    still alive but missing from the table is a reading that failed."""
    try:
        done = subprocess.run(
            [_PS, "-axo", "pgid=,rss="],
            capture_output=True,
            env={},
            timeout=min(5, budget),
            check=True,
            text=True,
        )
        total, seen = 0, False
        for line in done.stdout.splitlines():
            fields = line.split()
            if len(fields) == 2 and int(fields[0]) == group:
                total += int(fields[1]) * 1024
                seen = True
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    if seen:
        return total
    try:
        os.killpg(group, 0)
    except ProcessLookupError:
        return 0
    except PermissionError:
        return None
    return None


def _write_input(stream, payload):
    try:
        stream.write(payload)
        stream.close()
    except (BrokenPipeError, OSError):
        pass


def _kill_child_group(group):
    if group <= 1 or group == os.getpgrp():  # 0, 1, negatives and our own group are never sent
        return
    try:
        os.killpg(group, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
