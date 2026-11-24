"""Test-strength sweep of deny_watch.py: how the watchdog stops its stream (SIGTERM first, SIGKILL
for a stream that ignores it), and when it stops on its own (backstop reached, stream file gone,
SIGTERM to the watchdog, a failure inside the loop). A fake stream stands in for ``log``."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

WATCH = str(Path(__file__).parents[1] / "src" / "cairnwork" / "deny_watch.py")
BACKSTOP = 4096
_TOKENS: list[str] = []


def _pids(token: str) -> list[int]:
    out = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True).stdout
    return [
        int(row.split()[0])
        for row in out.splitlines()
        if token in row and str(os.getpid()) != row.split()[0] and "ps -axo" not in row
    ]


@pytest.fixture(autouse=True)
def _reap():
    yield
    while _TOKENS:
        for pid in _pids(_TOKENS.pop()):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def _gone(token: str, seconds: float = 5) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if not _pids(token):
            return True
        time.sleep(0.05)
    return False


def _child(tmp_path: Path, body: str) -> tuple[list[str], str, Path]:
    """A fake stream: writes its pid to ``ready`` after ``body`` ran, then sleeps."""
    token = "cwwatch-" + uuid.uuid4().hex
    ready = tmp_path / "ready"
    code = (
        f"# {token}\nimport os, signal, sys, time\n{body}\n"
        f"open({str(ready)!r}, 'w').write(str(os.getpid()))\ntime.sleep(60)\n"
    )
    _TOKENS.append(token)
    return [sys.executable, "-c", code], token, ready


def _start(tmp_path: Path, argv: list[str], *, path: Path | str | None = None, interval="0.05"):
    out = tmp_path / "stream"
    fd = os.open(out, os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        os.write(fd, b"x" * (BACKSTOP if path == "full" else 0))
        proc = subprocess.Popen(
            [sys.executable, "-I", WATCH, str(fd), str(path if path not in (None, "full") else out)]
            + [str(BACKSTOP), interval, *argv],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            pass_fds=(fd,),
        )
    finally:
        os.close(fd)
    return proc


def _eof(proc: subprocess.Popen) -> None:
    assert proc.stdin is not None
    proc.stdin.close()


def _wait_ready(ready: Path) -> None:
    end = time.monotonic() + 10
    while time.monotonic() < end:
        if ready.exists() and ready.read_text():
            return
        time.sleep(0.02)
    pytest.fail("the fake stream did not start")


def test_recorder_eof_stops_the_stream_and_the_watchdog_exits_zero(tmp_path):
    argv, token, ready = _child(tmp_path, "")
    proc = _start(tmp_path, argv)
    _wait_ready(ready)
    _eof(proc)
    assert proc.wait(timeout=10) == 0
    assert _gone(token)


def test_a_stream_gets_sigterm_first_so_it_can_finish_cleanly(tmp_path):
    marker = tmp_path / "terminated"
    body = (
        "def bye(*a):\n"
        f"    open({str(marker)!r}, 'w').write('term')\n"
        "    os._exit(0)\n"
        "signal.signal(signal.SIGTERM, bye)"
    )
    argv, token, ready = _child(tmp_path, body)
    proc = _start(tmp_path, argv)
    _wait_ready(ready)
    _eof(proc)
    assert proc.wait(timeout=10) == 0
    assert marker.exists() and _gone(token)


def test_a_stream_that_ignores_sigterm_is_killed(tmp_path):
    argv, token, ready = _child(tmp_path, "signal.signal(signal.SIGTERM, signal.SIG_IGN)")
    proc = _start(tmp_path, argv)
    _wait_ready(ready)
    _eof(proc)
    assert proc.wait(timeout=10) == 0
    assert _gone(token), "a stream that ignores SIGTERM outlived its watchdog"


def test_a_stream_file_exactly_at_the_backstop_stops_the_stream(tmp_path):
    argv, token, ready = _child(tmp_path, "")
    proc = _start(tmp_path, argv, path="full")  # stdin stays open: only the size can stop it
    _wait_ready(ready)
    assert proc.wait(timeout=5) == 0
    assert _gone(token)


def test_a_stream_file_that_cannot_be_looked_at_stops_the_stream(tmp_path):
    argv, token, ready = _child(tmp_path, "")
    proc = _start(tmp_path, argv, path=tmp_path / "missing")
    _wait_ready(ready)
    assert proc.wait(timeout=5) == 0
    assert _gone(token)


def test_sigterm_to_the_watchdog_stops_the_stream_and_exits_zero(tmp_path):
    argv, token, ready = _child(tmp_path, "")
    proc = _start(tmp_path, argv)
    _wait_ready(ready)
    proc.send_signal(signal.SIGTERM)
    assert proc.wait(timeout=5) == 0
    assert _gone(token)


def test_a_failure_inside_the_loop_still_stops_the_stream(tmp_path):
    argv, token, _ready = _child(tmp_path, "")
    proc = _start(tmp_path, argv, interval="not-a-number")
    assert proc.wait(timeout=10) != 0
    assert _gone(token), "the stream outlived a watchdog that failed in its loop"
