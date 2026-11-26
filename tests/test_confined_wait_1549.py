"""macOS before Python 3.13 has no os.waitid: the confined run waits for its leader through a
kqueue, still unreaped, and never ends in INTERNAL (Issue #1549, family of #1163)."""

import errno
import os
import select
import subprocess
import sys
import time
from typing import Any

import pytest

from cairnwork import confined, proc_wait

TOKEN = "t-1549"
kq: Any = select  # kqueue constants exist on BSD and macOS only
needs_kqueue = pytest.mark.skipif(not hasattr(select, "kqueue"), reason="kqueue hosts only")


def _state(pid: int) -> str:
    out = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True)  # noqa: S603, S607
    return out.stdout.strip()


def _spy(monkeypatch: Any) -> list[str]:
    real = confined._kill_group
    states: list[str] = []

    def kill(pgid: int) -> None:
        states.append(_state(pgid))
        real(pgid)

    monkeypatch.setattr(confined, "_kill_group", kill)
    monkeypatch.setattr(confined, "_sweep_survivors", lambda *_args: 0)
    return states


@pytest.fixture
def no_waitid(monkeypatch: Any) -> None:
    monkeypatch.delattr(os, "waitid", raising=False)


@needs_kqueue
def test_exited_leader_is_a_zombie_when_the_group_is_signalled(
    no_waitid: None, monkeypatch: Any, tmp_path: Any
):
    states = _spy(monkeypatch)
    env = {**os.environ, confined.RUN_TOKEN_VAR: TOKEN}
    code, timed_out, _ = confined.execute(
        [sys.executable, "-c", "raise SystemExit(7)"],
        tmp_path,
        env,
        10,
        tmp_path / "a.log",
        scope_roots=(tmp_path.parent,),
    )
    assert (code, timed_out) == (7, False)  # real exit status, not INTERNAL
    assert len(states) == 1
    assert states[0].startswith("Z")  # exited and not reaped: its pgid cannot be reused yet


@needs_kqueue
def test_timed_out_leader_is_running_when_the_group_is_signalled(
    no_waitid: None, monkeypatch: Any, tmp_path: Any
):
    states = _spy(monkeypatch)
    env = {**os.environ, confined.RUN_TOKEN_VAR: TOKEN}
    code, timed_out, _ = confined.execute(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        tmp_path,
        env,
        1,
        tmp_path / "b.log",
        scope_roots=(tmp_path.parent,),
    )
    assert (code, timed_out) == (None, True)
    assert len(states) == 1
    assert states[0] != "" and not states[0].startswith("Z")


@needs_kqueue
def test_already_exited_leader_is_seen_without_reaping(no_waitid: None):
    child = subprocess.Popen([sys.executable, "-c", "pass"])  # noqa: S603
    try:
        deadline = time.monotonic() + 30
        while not _state(child.pid).startswith("Z"):
            assert time.monotonic() < deadline, "child never became a zombie"
            time.sleep(0.05)
        assert proc_wait.wait_exited_unreaped(child.pid, 5) is True
        assert _state(child.pid).startswith("Z")  # still there to wait for
    finally:
        child.wait()


@needs_kqueue
def test_kqueue_wait_times_out_on_a_running_child(no_waitid: None):
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])  # noqa: S603
    try:
        assert proc_wait.wait_exited_unreaped(child.pid, 0.2) is False
    finally:
        child.kill()
        child.wait()


def test_a_host_with_neither_call_fails_loudly(no_waitid: None, monkeypatch: Any):
    monkeypatch.delattr(select, "kqueue", raising=False)
    with pytest.raises(NotImplementedError, match="cannot wait for a child unreaped"):
        proc_wait.wait_exited_unreaped(os.getpid(), 0)


@needs_kqueue
def test_a_child_reaped_by_wait_raises_like_waitid(no_waitid: None):
    child = subprocess.Popen([sys.executable, "-c", "pass"])  # noqa: S603
    child.wait()
    with pytest.raises(ChildProcessError):
        proc_wait.wait_exited_unreaped(child.pid, 1)


@needs_kqueue
def test_a_pid_that_never_existed_raises(no_waitid: None):
    child = subprocess.Popen([sys.executable, "-c", "pass"])  # noqa: S603
    child.wait()
    for pid in (child.pid + 1_000_000, 2**22 - 3):  # beyond any live pid
        with pytest.raises(ChildProcessError):
            proc_wait.wait_exited_unreaped(pid, 1)


@needs_kqueue
def test_any_other_kqueue_error_raises_not_exited(no_waitid: None, monkeypatch: Any):
    class FakeEvent:
        flags = kq.KQ_EV_ERROR
        fflags = 0
        data = errno.EPERM

    class FakeQueue:
        def control(self, *_args: Any) -> list[Any]:
            return [FakeEvent()]

        def close(self) -> None:
            pass

    monkeypatch.setattr(select, "kqueue", FakeQueue)
    with pytest.raises(PermissionError):
        proc_wait.wait_exited_unreaped(os.getpid(), 1)


@needs_kqueue
def test_exit_after_registering_is_seen_and_zero_or_negative_timeouts_do_not_wait(no_waitid: None):
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.4)"])  # noqa: S603
    try:
        assert proc_wait.wait_exited_unreaped(child.pid, 0) is False
        assert proc_wait.wait_exited_unreaped(child.pid, -1) is False  # as the waitid poll does
        assert proc_wait.wait_exited_unreaped(child.pid, 10) is True
        assert _state(child.pid).startswith("Z")
    finally:
        child.wait()


@needs_kqueue
def test_a_live_pid_that_is_not_our_child_is_waited_on_not_rejected(no_waitid: None):
    # Recorded, not promised (proc_wait precondition: pid is the caller's own unreaped child):
    # the kqueue form cannot tell a foreign live pid from a child, so it just waits for its exit.
    assert proc_wait.wait_exited_unreaped(1, 0.1) is False  # init/launchd: alive, never ours
