"""Own a deny-log stream until its recorder closes the pipe or the backstop cap is reached.

The stream runs in this watchdog's process group, which the recorder ends on stop (also when the
watchdog was killed). If the recorder and the watchdog are both killed at once, nothing ends the
group: the stream may run on, but RLIMIT_FSIZE keeps its file at the 2x backstop
(test_both_killed_keeps_stream_within_rlimit pins that bound; #1095)."""

from __future__ import annotations

import os
import resource
import select
import signal
import subprocess
import sys

_STOP = False


def _request_stop(_signum, _frame) -> None:
    global _STOP
    _STOP = True


def _stop(stream: subprocess.Popen) -> None:
    if stream.poll() is not None:
        return
    stream.terminate()
    try:
        stream.wait(timeout=0.5)
    except subprocess.TimeoutExpired:
        stream.kill()
        stream.wait()


def _limit_file_size(backstop: int) -> None:
    resource.setrlimit(resource.RLIMIT_FSIZE, (backstop, backstop))


def main() -> int:
    """Run LOG, stopping it on recorder EOF or when the output reaches its backstop cap."""
    fd, path, backstop, interval, *argv = sys.argv[1:]
    signal.signal(signal.SIGTERM, _request_stop)
    stream = subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=int(fd),
        stderr=subprocess.DEVNULL,
        preexec_fn=lambda: _limit_file_size(int(backstop)),
    )
    os.close(int(fd))
    try:
        while not _STOP and stream.poll() is None:
            readable, _, _ = select.select([sys.stdin], [], [], float(interval))
            if readable and not os.read(sys.stdin.fileno(), 1):
                break
            try:
                if os.stat(path).st_size >= int(backstop):
                    break
            except OSError:
                break
        _stop(stream)
    finally:
        if stream.poll() is None:
            _stop(stream)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
