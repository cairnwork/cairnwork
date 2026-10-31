"""The run tag names the whole run root, so runs of two suites never share it (#533).

Two suites on one host each make a runs directory whose run root is named local_worker_fakes.TAG.
The tag entry used to be that last path component, so one suite's sweep stopped the other's
background process. The entry now carries the whole run root, and a run root with white space is
refused so the entry stays one word of a ps line.
"""

import os
import subprocess
import sys
import time

import pytest
from local_worker_fakes import TAG, make_runs
from local_worker_fakes import run_wrapper as run

CLEAN = b"LOCAL_WORKER_SWEEP clean\n"
SLEEPER = """
import os, time
tmp = os.environ["TMPDIR"]
with open(os.path.join(tmp, "pid.part"), "w") as fh:
    fh.write(str(os.getpid()))
os.replace(os.path.join(tmp, "pid.part"), os.path.join(tmp, "pid"))
os.chdir("/")  # from here on only its environment ties it to its run
end = time.time() + 120
while time.time() < end:
    time.sleep(0.05)
"""


def _venv_python(root, text):
    py = root / "venv" / "bin" / "python"
    py.parent.mkdir(parents=True, exist_ok=True)
    py.write_text(text)
    py.chmod(0o755)


def _wait_for(path, seconds=60.0):
    end = time.monotonic() + seconds
    while not path.exists():
        assert time.monotonic() < end, f"{path.name} never appeared"
        time.sleep(0.05)
    return path.read_text()


def _alive(pid):
    """Running, not a zombie waiting for launchd to reap it."""
    ps = subprocess.run(
        ["/bin/ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, check=False
    )
    return ps.returncode == 0 and "Z" not in ps.stdout


def _gone(pid, seconds=10.0):
    end = time.monotonic() + seconds
    while _alive(pid):
        if time.monotonic() > end:
            return False
        time.sleep(0.05)
    return True


def _start_sleeper(runs):
    """A process of the run root runs/TAG, started through the wrapper's pytest action (so with
    the run's environment) and left running; returns its pid."""
    root = runs / TAG
    tmp = root / "tmp"
    (tmp / "sleeper.py").write_text(SLEEPER)
    base = os.path.realpath(getattr(sys, "_base_executable", sys.executable))
    _venv_python(
        root,
        f"#!/bin/sh\n{base} {tmp / 'sleeper.py'} </dev/null >/dev/null 2>&1 &\nexit 0\n",
    )
    assert run(runs, "pytest", str(root)).returncode == 0
    return int(_wait_for(tmp / "pid"))


@pytest.mark.sandbox
def test_the_tag_entry_is_the_whole_run_root(tmp_path):
    runs = make_runs(tmp_path)
    root = runs / TAG
    _venv_python(root, '#!/bin/sh\nprintf "%s\\n" "$CAIRNWORK_LOCAL_RUN_TAG"\n')
    proc = run(runs, "pytest", str(root))
    assert proc.returncode == 0, proc.stdout
    assert proc.stdout == f"{root}\n".encode()  # not just the name, which two suites share


@pytest.mark.sandbox
def test_two_runs_named_alike_do_not_sweep_each_other(tmp_path):
    """#533: two suites, two runs directories, both run roots named TAG."""
    a = make_runs(tmp_path / "suite_a")
    b = make_runs(tmp_path / "suite_b")
    pid_b = _start_sleeper(b)  # outside its run root: found by its environment only
    try:
        assert run(a, "sweep", str(a / TAG)).stdout == CLEAN
        assert _alive(pid_b), "run A's sweep stopped run B's process"
    finally:
        swept = run(b, "sweep", str(b / TAG)).stdout
    assert swept == CLEAN
    assert _gone(pid_b), "run B's sweep did not find its own process by its environment"


def test_a_run_root_with_white_space_is_refused(tmp_path):
    runs = make_runs(tmp_path / "two words")
    proc = run(runs, "sweep", str(runs / TAG))
    assert proc.returncode == 64 and b"white space" in proc.stderr, proc.stderr
