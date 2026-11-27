"""Session cleanup regressions for hook_watch (#1146)."""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import kill_guard

import cairnwork.post_verified as post_verified


def _watcher() -> Path:
    return Path(post_verified.__file__).with_name("hook_watch.py")


def test_session_scan_skips_zombies(tmp_path):
    code = (
        "import os,sys,time\n"
        "import importlib.util\n"
        f"spec = importlib.util.spec_from_file_location('hook_watch', {str(_watcher())!r})\n"
        "hook_watch = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(hook_watch)\n"
        "child = os.fork()\n"
        "if child == 0: os._exit(0)\n"
        "time.sleep(0.3)\n"
        "start = time.monotonic()\n"
        "hook_watch._kill_session(kill_group=False)\n"
        "print(time.monotonic() - start)\n"
        "os.waitpid(child, 0)\n"
    )
    proc = subprocess.run(
        [sys.executable, "-I", "-c", code],
        capture_output=True,
        text=True,
        timeout=20,
        start_new_session=True,
        check=False,
    )
    # Linux keeps a zombie's session id, so without the skip the scan spins its full 3 s there;
    # macOS getsid fails for zombies, so this passes there either way.
    assert proc.returncode == 0, proc.stderr
    assert float(proc.stdout) < 1.5


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
            kill_guard.REAL_KILL(pid, signal.SIGKILL)  # the test's own detached process
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


def test_successful_hook_keeps_status_when_ps_is_not_on_path():
    """The listing uses an absolute ps and never kills the watchdog itself on a normal exit."""
    code, _out, _err = post_verified._run(["/usr/bin/true"], 10, {"PATH": "/nonexistent:/usr/bin"})
    assert code == 0


def test_runner_timeout_kills_setpgid_grandchild(tmp_path):
    pid_file = tmp_path / "gc.pid"
    pid = 0
    try:
        try:
            post_verified._run(
                [sys.executable, "-c", _hook_with_grandchild(pid_file, wait_for_parent=True)],
                2,
                dict(os.environ),
            )
        except subprocess.TimeoutExpired:
            pass
        else:
            raise AssertionError("the hook outlived its limit without a timeout")
        pid = _wait_for_pid(pid_file)
        _assert_gone(pid)
    finally:
        if pid:
            try:
                kill_guard.REAL_KILL(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_exit_scan_is_bounded_by_budget_with_hanging_ps(monkeypatch):
    import importlib.util

    spec = importlib.util.spec_from_file_location("hook_watch_1600", _watcher())
    assert spec is not None and spec.loader is not None
    hook_watch = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hook_watch)
    real_run = subprocess.run
    calls: list[tuple[float, float]] = []  # (start, latest end the timeout allows)

    def hanging_ps(argv, *args, **kwargs):
        if argv[0] != "/bin/ps":
            return real_run(argv, *args, **kwargs)
        now = time.monotonic()
        calls.append((now, now + kwargs["timeout"]))
        time.sleep(0.1)  # a slow ps that fails; the next call must fit in what is left
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    monkeypatch.setattr(hook_watch.subprocess, "run", hanging_ps)
    read_end, write_end = os.pipe()
    monkeypatch.setattr(sys, "stdin", os.fdopen(read_end, "rb", buffering=0))
    monkeypatch.setattr(sys, "argv", ["hook_watch", "30", "--", "/usr/bin/true"])
    try:
        assert hook_watch.main() == 0
    finally:
        os.close(write_end)
    assert calls
    first = calls[0][0]
    # the runner allows EXIT_SCAN_GRACE (1 s) for this scan; no ps call may run past 0.5 s
    assert max(end for _start, end in calls) <= first + 0.5 + 0.05, calls
    assert time.monotonic() - first < post_verified.EXIT_SCAN_GRACE
