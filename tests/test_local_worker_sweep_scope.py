"""The wrapper's sweep stops this run's processes and no others (#533).

Two suites on one host each make a runs directory whose run root is named local_worker_fakes.TAG.
Before #533 the sweep matched that name, so one suite's sweep killed the other's processes (F3).
It also matched the tag entry anywhere on a ps -E line, arguments included, so a process outside
every run whose command line names the entry was killed too (F4). Now the entry carries the whole
run root and must be in the process's environment; in the arguments column it is never killed and
the sweep is not clean.
"""

import os
import subprocess
import sys
import time

import pytest
from local_worker_fakes import TAG, make_runs
from local_worker_fakes import run_wrapper as run

TAG_VAR = "CAIRNWORK_LOCAL_RUN_TAG"
CLEAN = b"LOCAL_WORKER_SWEEP clean\n"
SLEEPER = """
import os, sys, time
if sys.argv[1:2] == ["blank"]:
    import ctypes  # clear every argument string in place, before the pid file says it is up
    libc = ctypes.CDLL(None)
    libc._NSGetArgv.restype = ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))
    libc._NSGetArgc.restype = ctypes.POINTER(ctypes.c_int)
    argv = libc._NSGetArgv().contents
    for i in range(libc._NSGetArgc().contents.value):
        ctypes.memset(argv[i], 0, len(ctypes.string_at(argv[i])))
    os.chdir("/")  # only its environment ties it to its run
tmp = os.environ["TMPDIR"]
with open(os.path.join(tmp, "pid.part"), "w") as fh:
    fh.write(str(os.getpid()))
os.replace(os.path.join(tmp, "pid.part"), os.path.join(tmp, "pid"))
if sys.argv[1:2] in (["leave"], ["churn"]):
    os.chdir("/")  # from here on only its environment ties it to its run
if sys.argv[1:2] == ["churn"]:
    import ctypes  # rewrite the last argument in place over and over
    libc = ctypes.CDLL(None)
    libc._NSGetArgv.restype = ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))
    libc._NSGetArgc.restype = ctypes.POINTER(ctypes.c_int)
    where = libc._NSGetArgv().contents[libc._NSGetArgc().contents.value - 1]
    width, n = len(sys.argv[-1]), 0
    while True:
        n += 1
        ctypes.memmove(where, str(n % 10**width).zfill(width).encode(), width)
        time.sleep(0.0003)
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


def _start_sleeper(runs, *args):
    """A process of the run root runs/TAG, started through the wrapper's pytest action (so with
    the run's environment) and left running; returns its pid."""
    root = runs / TAG
    tmp = root / "tmp"
    (tmp / "sleeper.py").write_text(SLEEPER)
    base = os.path.realpath(getattr(sys, "_base_executable", sys.executable))
    tail = " ".join(args)
    _venv_python(
        root,
        f"#!/bin/sh\n{base} {tmp / 'sleeper.py'} {tail} </dev/null >/dev/null 2>&1 &\nexit 0\n",
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
    """#533 F3: two suites, two runs directories, both run roots named TAG."""
    a = make_runs(tmp_path / "suite_a")
    b = make_runs(tmp_path / "suite_b")
    pid_b = _start_sleeper(b, "leave")  # outside its run root: found by its environment only
    try:
        assert run(a, "sweep", str(a / TAG)).stdout == CLEAN
        assert _alive(pid_b), "run A's sweep stopped run B's process"
    finally:
        swept = run(b, "sweep", str(b / TAG)).stdout
    assert swept == CLEAN
    assert _gone(pid_b), "run B's sweep did not find its own process by its environment"


@pytest.mark.sandbox
def test_a_process_whose_arguments_keep_changing_makes_the_sweep_unclean_and_is_not_stopped(
    tmp_path,
):
    """The wrapper cannot tell whose it is (its two ps reads never agree): it is not stopped and
    the sweep is not clean."""
    runs = make_runs(tmp_path)
    pid = _start_sleeper(runs, "churn", "0" * 12)
    try:
        out = run(runs, "sweep", str(runs / TAG)).stdout
        assert out.startswith(b"LOCAL_WORKER_SWEEP unclean"), out
        assert str(pid).encode() in out.split()[2:], out
        assert _alive(pid), "the sweep stopped a process it could not tell"
    finally:
        os.kill(pid, 9)
    assert _gone(pid)


@pytest.mark.sandbox
def test_a_process_of_the_run_with_blank_arguments_makes_the_sweep_unclean(tmp_path):
    """It left the run root and cleared its arguments: the sweep cannot tell it apart, so it is not
    stopped and the sweep is not clean (it must never come out clean while the process lives)."""
    runs = make_runs(tmp_path)
    pid = _start_sleeper(runs, "blank", *["x"] * 40)  # more arguments than environment entries
    try:
        out = run(runs, "sweep", str(runs / TAG)).stdout
        assert out.startswith(b"LOCAL_WORKER_SWEEP unclean"), out
        assert str(pid).encode() in out.split()[2:], out
        assert _alive(pid), "the sweep stopped a process it could not tell"
    finally:
        os.kill(pid, 9)
    assert _gone(pid)


@pytest.mark.skipif(sys.platform != "darwin", reason="the sweep reads ps -E and lsof as on macOS")
def test_a_command_line_that_names_the_tag_entry_makes_the_sweep_unclean(tmp_path):
    """#533 F4: the entry in a process's arguments, not its environment: not killed, and the sweep
    is not clean (the arguments column cannot be told apart from this run's own)."""
    runs = make_runs(tmp_path)
    root = runs / TAG
    words = [f"{TAG_VAR}={TAG}", f"{TAG_VAR}={root}"]  # the entry as it was and as it is now
    env = {k: v for k, v in os.environ.items() if k != TAG_VAR}
    other = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(120)", *words],
        cwd="/",
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        seen, end = "", time.monotonic() + 10
        while words[1] not in seen:  # it exec'd and ps can read its arguments: no vacuous pass
            assert time.monotonic() < end, f"ps never showed the arguments: {seen!r}"
            seen = subprocess.run(
                ["/bin/ps", "-ww", "-o", "command=", "-p", str(other.pid)],
                capture_output=True,
                text=True,
                check=False,
            ).stdout
            time.sleep(0.05)
        out = run(runs, "sweep", str(root)).stdout
        assert out.startswith(b"LOCAL_WORKER_SWEEP unclean"), out
        assert str(other.pid).encode() in out.split()[2:], out
        assert other.poll() is None, "the sweep stopped a process outside the run"
    finally:
        other.kill()
        other.wait()


@pytest.mark.skipif(sys.platform != "darwin", reason="the sweep reads ps -E and lsof as on macOS")
def test_the_entry_in_the_arguments_does_not_spare_a_process_whose_cwd_is_in_the_run(tmp_path):
    """Its cwd inside the run root makes it this run's: the entry in its arguments (unclean on its
    own) does not keep it alive, and once it is gone the sweep is clean."""
    runs = make_runs(tmp_path)
    root = runs / TAG
    env = {k: v for k, v in os.environ.items() if k != TAG_VAR}
    other = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(120)", f"{TAG_VAR}={root}"],
        cwd=root,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        seen, end = "", time.monotonic() + 10
        while str(root) not in seen:  # it exec'd and ps can read its arguments: no vacuous pass
            assert time.monotonic() < end, f"ps never showed the arguments: {seen!r}"
            seen = subprocess.run(
                ["/bin/ps", "-ww", "-o", "command=", "-p", str(other.pid)],
                capture_output=True,
                text=True,
                check=False,
            ).stdout
            time.sleep(0.05)
        assert run(runs, "sweep", str(root)).stdout == CLEAN
        assert _gone(other.pid), "the sweep left a process whose cwd is in the run root"
    finally:
        other.kill()
        other.wait()


def test_a_run_root_with_white_space_is_refused(tmp_path):
    runs = make_runs(tmp_path / "two words")
    proc = run(runs, "sweep", str(runs / TAG))
    assert proc.returncode == 64 and b"white space" in proc.stderr, proc.stderr
