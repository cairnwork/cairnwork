"""Session cleanup regressions for hook_watch (#1146)."""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import cairnwork.post_verified as post_verified


def _watcher() -> Path:
    return Path(post_verified.__file__).with_name("hook_watch.py")


def _spawn_hook(hook_code: str, seconds: int = 30) -> subprocess.Popen:
    return subprocess.Popen(
        [
            sys.executable,
            "-I",
            str(_watcher()),
            str(seconds),
            "--",
            sys.executable,
            "-c",
            hook_code,
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )


def _wait_for_pid(pid_file: Path) -> int:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if pid_file.exists():
            return int(pid_file.read_text())
        time.sleep(0.02)
    raise AssertionError("grandchild did not start")


def _assert_gone(pid: int) -> None:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)
    raise AssertionError(f"process {pid} survived watchdog cleanup")


def _cleanup(proc: subprocess.Popen, *pids: int) -> None:
    for pid in pids:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if proc.poll() is None:
        proc.kill()
    proc.wait(timeout=5)


def _hook_with_grandchild(pid_file: Path, wait_for_parent: bool) -> str:
    child = (
        "import os,time; os.setpgid(0,0); "
        f"open({str(pid_file)!r}, 'w').write(str(os.getpid())); time.sleep(30)"
    )
    wait = f"while not os.path.exists({str(pid_file)!r}):\n time.sleep(0.01)\n"
    if wait_for_parent:
        wait += "time.sleep(30)\n"
    return (
        "import subprocess,sys,time,os\n"
        f"subprocess.Popen([sys.executable, '-c', {child!r}])\n{wait}"
    )


def test_normal_hook_exit_kills_setpgid_grandchild(tmp_path):
    pid_file = tmp_path / "grandchild.pid"
    proc = _spawn_hook(_hook_with_grandchild(pid_file, False))
    grandchild = None
    try:
        grandchild = _wait_for_pid(pid_file)
        proc.wait(timeout=5)
        assert proc.returncode == 0
        _assert_gone(grandchild)
    finally:
        _cleanup(proc, *([grandchild] if grandchild else []))


def test_deadline_kills_setpgid_grandchild(tmp_path):
    pid_file = tmp_path / "grandchild.pid"
    proc = _spawn_hook(_hook_with_grandchild(pid_file, True), seconds=1)
    grandchild = None
    try:
        grandchild = _wait_for_pid(pid_file)
        proc.wait(timeout=9)
        assert proc.returncode == -signal.SIGKILL
        _assert_gone(grandchild)
    finally:
        _cleanup(proc, *([grandchild] if grandchild else []))


def test_closed_runner_stdin_kills_setpgid_grandchild(tmp_path):
    pid_file = tmp_path / "grandchild.pid"
    proc = _spawn_hook(_hook_with_grandchild(pid_file, True))
    grandchild = None
    try:
        grandchild = _wait_for_pid(pid_file)
        assert proc.stdin is not None
        proc.stdin.close()
        proc.wait(timeout=5)
        assert proc.returncode == -signal.SIGKILL
        _assert_gone(grandchild)
    finally:
        _cleanup(proc, *([grandchild] if grandchild else []))


def test_different_session_process_survives_watchdog(tmp_path):
    pid_file = tmp_path / "separate-session.pid"
    child = f"import os,time; open({str(pid_file)!r}, 'w').write(str(os.getpid())); time.sleep(30)"
    hook = (
        "import subprocess,sys,time,os\n"
        f"subprocess.Popen([sys.executable, '-c', {child!r}], start_new_session=True)\n"
        f"while not os.path.exists({str(pid_file)!r}):\n time.sleep(0.01)\n"
    )
    proc = _spawn_hook(hook)
    separate_pid = None
    try:
        separate_pid = _wait_for_pid(pid_file)
        proc.wait(timeout=5)
        assert proc.returncode == 0
        os.kill(separate_pid, 0)
    finally:
        _cleanup(proc, *([separate_pid] if separate_pid else []))


def test_ordinary_hook_exit_code_and_stdout_are_unchanged():
    code, out, _err = post_verified._run(
        [sys.executable, "-c", "import sys; print('ordinary'); sys.exit(7)"],
        10,
        dict(os.environ),
    )
    assert code == 7 and out == b"ordinary\n"
