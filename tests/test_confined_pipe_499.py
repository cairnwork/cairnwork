"""Issue #499: a pipe reader must not make confined cleanup hang."""

from __future__ import annotations

import os
import sys
import threading
import time
from typing import Any

from cairnwork import confined, survivor_argv

TOKEN = "pipe-499-token"


def test_blocked_copier_is_counted_and_cleanup_returns(monkeypatch: Any, tmp_path: Any):
    child_code = (
        "import subprocess,sys; "
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'], "
        "start_new_session=True); open(sys.argv[1],'w').write(str(p.pid))"
    )
    real_join = threading.Thread.join
    real_start = threading.Thread.start
    threads: list[threading.Thread] = []

    def capture_start(thread: threading.Thread) -> None:
        threads.append(thread)
        real_start(thread)

    def bounded_join(thread: threading.Thread, timeout: float | None = None) -> None:
        return real_join(thread, min(timeout, 0.1) if timeout is not None else 0.1)

    monkeypatch.setattr(threading.Thread, "join", bounded_join)
    monkeypatch.setattr(threading.Thread, "start", capture_start)
    monkeypatch.setattr(confined, "_sweep_survivors", lambda *_args: survivor_argv.Survivors(0))
    env = {**os.environ, confined.RUN_TOKEN_VAR: TOKEN}
    started = time.monotonic()
    _returncode, _timed_out, survivors = confined.execute(
        [sys.executable, "-c", child_code, str(tmp_path / "child.pid")],
        tmp_path,
        env,
        5,
        tmp_path / "blocked.log",
    )
    elapsed = time.monotonic() - started
    child_pid = None
    try:
        child_pid = int((tmp_path / "child.pid").read_text())
        assert elapsed < 3
        assert survivors == 1
        assert survivors.reasons == ("output copier still reading",)
        assert survivor_argv.listing(survivors) == " (reason: output copier still reading)"
    finally:
        if child_pid is not None:
            os.kill(child_pid, 9)
        for thread in threads:
            real_join(thread, 3)
            assert not thread.is_alive()


def test_completed_copier_closes_pipe_and_reports_no_survivor(monkeypatch: Any, tmp_path: Any):
    streams = []
    copy = confined.copy_capped

    def record_source(source: Any, target: Any) -> None:
        streams.append(source)
        copy(source, target)

    monkeypatch.setattr(confined, "copy_capped", record_source)
    monkeypatch.setattr(confined, "_sweep_survivors", lambda *_args: 0)
    env = {**os.environ, confined.RUN_TOKEN_VAR: TOKEN}
    returncode, timed_out, survivors = confined.execute(
        [sys.executable, "-c", "print('done')"], tmp_path, env, 5, tmp_path / "normal.log"
    )
    assert (returncode, timed_out, survivors) == (0, False, 0)
    assert streams and streams[0].closed


def test_reaped_leader_group_is_not_signalled(monkeypatch: Any, tmp_path: Any):
    signalled = []
    swept_groups = []
    monkeypatch.setattr(confined, "_kill_group", signalled.append)

    def sweep(_token, group, *_args):
        swept_groups.append(group)
        return survivor_argv.Survivors(0)

    monkeypatch.setattr(confined, "_sweep_survivors", sweep)
    env = {**os.environ, confined.RUN_TOKEN_VAR: TOKEN}
    confined.execute([sys.executable, "-c", "pass"], tmp_path, env, 5, tmp_path / "group.log")
    assert signalled == []
    assert swept_groups == [-1]
