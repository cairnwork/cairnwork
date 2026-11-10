"""The deny stream and its cap outlive no recorder, even after SIGKILL."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

_RECORDERS: list[subprocess.Popen] = []


@pytest.fixture(autouse=True)
def _reap_fake_log(tmp_path):
    """A failing test must not leave its fake log writing: stop it if it is still ours."""
    yield
    while _RECORDERS:
        recorder = _RECORDERS.pop()
        if recorder.poll() is None:
            recorder.kill()
            recorder.wait(timeout=5)
    pid_file = tmp_path / "log-pid"
    text = pid_file.read_text() if pid_file.exists() else ""
    if not text:
        return
    pid = int(text.split()[0])
    argv = subprocess.run(
        ["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True
    ).stdout
    if str(tmp_path / "log") in argv:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def _launch(tmp_path: Path, *, cap: int = 32 << 20, hold: bool = True, mode: str = ""):
    pid_file = tmp_path / "log-pid"
    fast_file = tmp_path / "start-writing-fast"
    fake_log = tmp_path / "log"
    fake_log.write_text(
        "#!" + sys.executable + "\n"
        "import os, time\n"
        "with open(os.environ['FAKE_LOG_PID'], 'w') as handle:\n"
        "    handle.write(f'{os.getpid()} {os.getppid()}')\n"
        "    handle.flush()\n"
        "os.write(1, b'ready\\n')\n"
        "while not os.path.exists(os.environ['FAKE_LOG_FAST']): time.sleep(0.005)\n"
        "deadline = time.monotonic() + 60  # never outlives a failed test for long\n"
        "while time.monotonic() < deadline:\n"
        "    try: os.write(1, b'x' * 1024 + b'\\n')\n"
        "    except OSError: break\n"
        "    time.sleep(0.001)\n"
    )
    fake_log.chmod(0o700)
    code = (
        "import sys, time; sys.path.insert(0, sys.argv[1]); "
        "from pathlib import Path; "
        "import cairnwork.deny_channel as d; "
        "d.LOG=sys.argv[2]; d.MAX_STREAM_BYTES=int(sys.argv[3]); "
        "d._SizeGuard.run=lambda self: None; "
        "d.tempfile.tempdir=sys.argv[4]; "
        "ctx=d.recording('cwvtag-0123456789abcdef', lambda argv, log: ''); "
        "box=ctx.__enter__(); print('ready', flush=True)\n"
        "m=sys.argv[5]; go=Path(sys.argv[4])/'go'; end=time.time()+30\n"
        "while m!='normal' and time.time()<end and not (m=='gate' and go.exists()):\n"
        "    time.sleep(0.01)\n"
        "ctx.__exit__(None,None,None); print(box.evidence.state, flush=True)"
    )
    proc = subprocess.Popen(
        [
            sys.executable,
            "-I",
            "-c",
            code,
            str(Path(__file__).parents[1] / "src"),
            str(fake_log),
            str(cap),
            str(tmp_path),
            mode or ("hold" if hold else "normal"),
        ],
        env={**os.environ, "FAKE_LOG_PID": str(pid_file), "FAKE_LOG_FAST": str(fast_file)},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    _RECORDERS.append(proc)
    return proc, pid_file, fast_file


def _wait_file(path: Path, seconds: float = 5) -> str:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if path.exists() and path.read_text():
            return path.read_text()
        time.sleep(0.02)
    pytest.fail(f"timed out waiting for {path}")


def _wait_gone(pid: int, seconds: float = 5) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.02)
    return False


def _stream_file(tmp_path: Path) -> Path:
    end = time.monotonic() + 5
    while time.monotonic() < end:
        files = list(tmp_path.glob("cairnwork-deny.*/stream.ndjson"))
        if files:
            return files[0]
        time.sleep(0.02)
    pytest.fail("recorder did not create the stream file")


def _kill_recorder(proc: subprocess.Popen) -> None:
    proc.send_signal(signal.SIGKILL)
    proc.wait(timeout=5)


def test_sigkill_closes_stream_and_file_stops_growing(tmp_path):
    proc, pid_file, fast_file = _launch(tmp_path)
    pid, _ppid = map(int, _wait_file(pid_file).split())
    stream = _stream_file(tmp_path)
    _kill_recorder(proc)
    fast_file.touch()

    assert _wait_gone(pid, 5), "fake log process remained after recorder SIGKILL"
    size = stream.stat().st_size
    time.sleep(0.1)
    assert stream.stat().st_size == size, "stream file continued growing after recorder death"


def test_sigkill_keeps_stream_within_cap(tmp_path):
    cap = 64 * 1024
    proc, pid_file, fast_file = _launch(tmp_path, cap=cap)
    pid, _ppid = map(int, _wait_file(pid_file).split())
    stream = _stream_file(tmp_path)
    fast_file.touch()

    observed, gone, end = [], False, time.monotonic() + 5
    while time.monotonic() < end:
        observed.append(stream.stat().st_size)
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            gone = True
            break
        time.sleep(0.02)
    assert gone, "fake log process remained after watchdog hit its backstop"
    assert proc.poll() is None, "recorder exited before the watchdog capped the stream"
    observed.append(stream.stat().st_size)
    assert max(observed) <= 2 * cap, f"stream exceeded {2 * cap} bytes: {max(observed)}"
    _kill_recorder(proc)


def test_normal_exit_reaps_log_and_runs_it_under_watchdog(tmp_path):
    proc, pid_file, _fast_file = _launch(tmp_path, hold=False)
    pid, ppid = map(int, _wait_file(pid_file).split())

    assert ppid != proc.pid, "fake log was not started by the watchdog"
    proc.communicate(timeout=5)
    assert _wait_gone(pid, 5), "fake log process remained after recording() exited"


def test_a_killed_watchdog_still_has_its_stream_stopped(tmp_path):
    proc, pid_file, fast_file = _launch(tmp_path, mode="gate")
    pid, watchdog = map(int, _wait_file(pid_file).split())
    fast_file.touch()
    os.kill(watchdog, signal.SIGKILL)  # the watchdog dies without reaping log stream

    (tmp_path / "go").touch()  # the recorder now finishes normally
    out, _err = proc.communicate(timeout=20)

    assert _wait_gone(pid, 5), "log stream outlived its killed watchdog"
    assert out.split()[-1] == "UNAVAILABLE", out


def test_both_killed_keeps_stream_within_rlimit(tmp_path):
    cap = 64 * 1024
    proc, pid_file, fast_file = _launch(tmp_path, cap=cap, mode="gate")
    pid, watchdog = map(int, _wait_file(pid_file).split())
    stream = _stream_file(tmp_path)

    os.kill(watchdog, signal.SIGKILL)
    _kill_recorder(proc)

    fast_file.touch()

    assert _wait_gone(pid, 20), "fake log process remained after watchdog and recorder SIGKILL"
    size = stream.stat().st_size
    assert size <= 2 * cap, f"stream exceeded {2 * cap} bytes: {size}"

