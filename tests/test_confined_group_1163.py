"""The group is signalled while the leader is still unreaped, so its pgid cannot have been
handed to an unrelated process (Issue #1163, follow-up to #499)."""

import os
import sys
from typing import Any

import pytest

from cairnwork import confined

# macOS before Python 3.13 has no os.waitid; test_confined_wait_1549 covers that path (#1549)
pytestmark = pytest.mark.skipif(not hasattr(os, "waitid"), reason="os.waitid needed to observe")

TOKEN = "t-1163"


def _spy(monkeypatch: Any) -> list[Any]:
    real = confined._kill_group
    states: list[Any] = []

    def kill(pgid: int) -> None:
        try:
            seen = os.waitid(os.P_PID, pgid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
        except ChildProcessError:
            seen = "reaped"
        states.append(seen)
        real(pgid)

    monkeypatch.setattr(confined, "_kill_group", kill)
    monkeypatch.setattr(confined, "_sweep_survivors", lambda *_args: 0)
    return states


def test_group_is_signalled_before_an_exited_leader_is_reaped(monkeypatch: Any, tmp_path: Any):
    states = _spy(monkeypatch)
    env = {**os.environ, confined.RUN_TOKEN_VAR: TOKEN}
    confined.execute(
        [sys.executable, "-c", "pass"],
        tmp_path,
        env,
        5,
        tmp_path / "a.log",
        scope_roots=(tmp_path.parent,),
    )
    assert len(states) == 1
    assert states[0] != "reaped"  # reaped first: its pgid was free to be reused
    assert states[0] is not None  # exited, and still waitable (a zombie)


def test_timed_out_leader_is_still_running_when_the_group_is_signalled(
    monkeypatch: Any, tmp_path: Any
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
    assert states == [None]  # still running: waitid(WNOHANG) has nothing to report


def test_exit_status_survives_the_unreaped_wait(monkeypatch: Any, tmp_path: Any):
    _spy(monkeypatch)
    env = {**os.environ, confined.RUN_TOKEN_VAR: TOKEN}
    code, timed_out, _ = confined.execute(
        [sys.executable, "-c", "raise SystemExit(7)"],
        tmp_path,
        env,
        5,
        tmp_path / "c.log",
        scope_roots=(tmp_path.parent,),
    )
    assert (code, timed_out) == (7, False)
