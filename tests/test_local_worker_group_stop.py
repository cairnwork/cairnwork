"""subprocess_invoker stops the child's whole process group on every exit (#741).

Only a stand-in child is used: it starts a short `sleep` grandchild and writes its pid to a file.
The test signals nothing but that grandchild's own group (through the code under test)."""

import os
import signal
import sys
import time

import pytest

from experimental.local_worker import tools

SCRIPT = (
    "import subprocess, sys, time\n"
    "p = subprocess.Popen(['sleep', '25'])\n"
    "open(sys.argv[1], 'w').write(str(p.pid))\n"
    "time.sleep(float(sys.argv[2]))\n"
)


def _grandchild(pidfile) -> int:
    for _ in range(100):
        if pidfile.exists() and pidfile.read_text():
            return int(pidfile.read_text())
        time.sleep(0.05)
    raise AssertionError("stand-in did not start")


def _gone(pid: int) -> bool:
    for _ in range(60):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        time.sleep(0.05)
    return False


def _cleanup(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGKILL)  # only the stand-in's own sleep, if the test failed
    except OSError:
        pass


def test_normal_exit_stops_the_grandchild(tmp_path):
    pidfile = tmp_path / "pid"
    invoke = tools.subprocess_invoker()
    rc, _, _ = invoke([sys.executable, "-c", SCRIPT, str(pidfile), "0.5"], None, 10.0)
    pid = _grandchild(pidfile)
    try:
        assert rc == 0
        assert _gone(pid)
    finally:
        _cleanup(pid)


def test_timeout_stops_the_grandchild(tmp_path):
    pidfile = tmp_path / "pid"
    invoke = tools.subprocess_invoker()
    rc, _, _ = invoke([sys.executable, "-c", SCRIPT, str(pidfile), "20"], None, 1.0)
    pid = _grandchild(pidfile)
    try:
        assert rc is None
        assert _gone(pid)
    finally:
        _cleanup(pid)


def test_an_exception_in_communicate_stops_the_grandchild(tmp_path, monkeypatch):
    pidfile = tmp_path / "pid"
    real = tools.subprocess.Popen

    class Popen(real):
        def communicate(self, *args, **kwargs):
            _grandchild(pidfile)
            raise KeyboardInterrupt

    monkeypatch.setattr(tools.subprocess, "Popen", Popen)
    invoke = tools.subprocess_invoker()
    with pytest.raises(KeyboardInterrupt):
        invoke([sys.executable, "-c", SCRIPT, str(pidfile), "20"], None, 10.0)
    pid = _grandchild(pidfile)
    try:
        assert _gone(pid)
    finally:
        _cleanup(pid)
