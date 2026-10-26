"""#233: orphaned git child processes spawned during verifier checkout are cleaned by recovery."""

import os
import shutil
import signal
import subprocess
import sys
import time

import pytest
from conftest import commit_change
from test_deferred_tests import REGRESSION, make_test_commit
from test_recovery_leftovers import (
    CALC,
    DRIVER,
    SLOW,
    _assert_clean_and_logs_kept,
    _recover,
)


@pytest.fixture
def p2p_project(repo):
    commit_change(
        repo,
        {
            "src/calc.py": CALC,
            "tests/conftest.py": (
                "import sys, pathlib\n"
                "sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / 'src'))\n"
            ),
            "tests/test_slow.py": SLOW,
        },
    )
    return repo, *make_test_commit(repo, {"tests/test_regression.py": REGRESSION})


@pytest.mark.sandbox
@pytest.mark.parametrize("step", ["q", "r"])
def test_sigkill_during_p2p_orphan_git_is_cleaned(p2p_project, tmp_path, step):
    repo, base, tests = p2p_project
    real_git = shutil.which("git")
    assert real_git

    wrapper_bin = tmp_path / "bin"
    wrapper_bin.mkdir(parents=True, exist_ok=True)
    wrapper_git = wrapper_bin / "git"
    pid_file = tmp_path / "wrapper.pid"
    done_file = tmp_path / "wrapper.done"

    # A Python wrapper, not /bin/sh: macOS hides the environment of Apple platform binaries
    # (/bin/sh, /bin/sleep) from ps, so a shell wrapper would be invisible to the sweep even
    # when marked. The real git it would start is visible (the /usr/bin/git shim execs it).
    script = f"""#!{sys.executable}
import os, subprocess, sys, time
args = sys.argv[1:]
if "clone" in args and any("-{step}-v" in a for a in args):
    with open({str(pid_file)!r}, "w") as f:
        f.write(str(os.getpid()))
    time.sleep(2)
    rc = subprocess.run([{real_git!r}, *args]).returncode
    with open({str(done_file)!r}, "w") as f:
        f.write("done")
    sys.exit(rc)
os.execv({real_git!r}, [{real_git!r}, *args])
"""
    wrapper_git.write_text(script)
    wrapper_git.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{wrapper_bin}:{env.get('PATH', '')}"

    state = tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)

    with open(state / "driver.err", "w") as err:
        proc = subprocess.Popen(
            [sys.executable, str(DRIVER), str(repo), str(state), base, tests, step],
            stdout=subprocess.DEVNULL,
            stderr=err,
            env=env,
        )

    deadline = time.monotonic() + 120
    wrapper_pid = None
    while time.monotonic() < deadline:
        if pid_file.exists():
            text = pid_file.read_text().strip()
            if text:
                wrapper_pid = int(text)
                break
        assert proc.poll() is None, (state / "driver.err").read_text()[-2000:]
        time.sleep(0.05)

    assert wrapper_pid is not None, "wrapper.pid must appear"

    os.kill(proc.pid, signal.SIGKILL)
    proc.wait()

    (decision,) = _recover(state)

    # In base code, wrapper is an untracked alive orphan, failing this assertion.
    with pytest.raises(ProcessLookupError):
        os.kill(wrapper_pid, 0)

    assert decision.detail == ""
    _assert_clean_and_logs_kept(state / "runs", decision.attempt_id)

    wait_end = time.monotonic() + 5
    while time.monotonic() < wait_end:
        assert not done_file.exists(), "wrapper.done must never appear"
        assert not list((state / "runs").glob(f"{decision.attempt_id}-*.work"))
        time.sleep(0.5)
