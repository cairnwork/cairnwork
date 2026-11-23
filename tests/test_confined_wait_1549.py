"""macOS before Python 3.13 has no os.waitid: the confined run waits for its leader through a
kqueue, still unreaped, and never ends in INTERNAL (Issue #1549, family of #1163)."""

import os
import select
import subprocess
import sys
from typing import Any

import pytest

from cairnwork import confined, proc_wait

TOKEN = "t-1549"
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
        [sys.executable, "-c", "raise SystemExit(7)"], tmp_path, env, 10, tmp_path / "a.log"
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
        [sys.executable, "-c", "import time; time.sleep(60)"], tmp_path, env, 1, tmp_path / "b.log"
    )
    assert (code, timed_out) == (None, True)
    assert len(states) == 1
    assert states[0] != "" and not states[0].startswith("Z")


@needs_kqueue
def test_already_exited_leader_is_seen_without_reaping(no_waitid: None):
    child = subprocess.Popen([sys.executable, "-c", "pass"])  # noqa: S603
    try:
        while not _state(child.pid).startswith("Z"):
            pass
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
