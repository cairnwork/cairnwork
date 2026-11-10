"""Regression coverage for issue #1125 hook watchdog behavior."""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import cairnwork.post_verified as post_verified


def test_v5_post_verified_hook_does_not_outlive_a_killed_runner(tmp_path):
    pid_file = tmp_path / "hook.pid"
    hook_code = (
        f"import os,time; open({str(pid_file)!r}, 'w').write(str(os.getpid())); time.sleep(15)"
    )
    runner_code = (
        "import sys; from cairnwork.post_verified import _run; "
        f"_run([sys.executable, '-c', {hook_code!r}], 30, {{}})"
    )
    runner = subprocess.Popen([sys.executable, "-c", runner_code])
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not pid_file.exists():
        time.sleep(0.05)
    assert pid_file.exists(), "hook did not start"
    hook_pid = int(pid_file.read_text())
    os.kill(runner.pid, signal.SIGKILL)
    runner.wait(timeout=5)

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(hook_pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        os.kill(hook_pid, signal.SIGKILL)
        raise AssertionError("hook outlived the killed runner")


def test_hook_exit_code_passes_through():
    code, out, _err = post_verified._run(
        [sys.executable, "-c", "import sys; print('hello'); sys.exit(3)"],
        10,
        dict(os.environ),
    )
    assert code == 3 and out == b"hello\n"


def test_deadline_ends_hook_without_runner(tmp_path):
    watcher = Path(post_verified.__file__).with_name("hook_watch.py")
    proc = subprocess.Popen(
        [
            sys.executable,
            "-I",
            str(watcher),
            "1",
            "--",
            sys.executable,
            "-c",
            "import time; time.sleep(30)",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        proc.wait(timeout=8)
        assert proc.returncode == -signal.SIGKILL
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_the_hook_sees_only_the_env_it_was_given_whatever_the_locale():
    # no LANG/LC_ALL: the watchdog interpreter would add LC_CTYPE (PEP 538) for the hook
    code, out, _err = post_verified._run(["/usr/bin/env"], 10, {"PATH": "/usr/bin:/bin"})
    keys = {line.split(b"=", 1)[0].decode() for line in out.splitlines() if b"=" in line}
    assert code == 0 and keys <= {"PATH", "PWD", "SHLVL", "_"}, keys


def test_a_hook_killed_by_a_signal_reports_128_plus_the_signal():
    code, _out, _err = post_verified._run(
        [sys.executable, "-c", "import os, signal; os.kill(os.getpid(), signal.SIGTERM)"],
        10,
        dict(os.environ),
    )
    assert code == 128 + signal.SIGTERM


def test_an_exec_failure_keeps_its_reason(tmp_path):
    binary = tmp_path / "not-a-program"
    binary.write_bytes(b"\x00\x01\x02\x03")
    binary.chmod(0o755)
    code, _out, err = post_verified._run([str(binary)], 10, {"PATH": "/usr/bin:/bin"})
    assert code != 0 and b"Exec format error" in err, (code, err)
