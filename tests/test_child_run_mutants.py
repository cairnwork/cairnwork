"""Focused regression checks for run_child's process cleanup contract."""

import signal

import cairnwork.child_run as child_run


class _Stream:
    def read(self, _size):
        return b""

    def write(self, _data):
        pass

    def close(self):
        pass


class _Process:
    pid = 123
    returncode = 0
    stdout = _Stream()
    stdin = _Stream()

    def wait(self, timeout=None):
        return 0


def _setup(monkeypatch, *, alive=(), fail_start=False):
    events = []
    process = _Process()

    def popen(*_args, **kwargs):
        events.append(("popen", kwargs))
        return process

    class Thread:
        def __init__(self, target, args=(), daemon=None):
            self.target = target
            self.args = args
            self.started = False

        def start(self):
            events.append("start")
            if fail_start:
                raise RuntimeError("thread start failed")
            self.started = True
            self.target(*self.args)

        def join(self, timeout=None):
            events.append("join")

        def is_alive(self):
            # Only the named threads stay alive, so each liveness check is pinned on its own.
            return ("writer" if self.target is child_run._write_input else "reader") in alive

    monkeypatch.setattr(child_run.subprocess, "Popen", popen)
    monkeypatch.setattr(child_run.threading, "Thread", Thread)
    monkeypatch.setattr(child_run.os, "killpg", lambda pid, sig: events.append(("kill", sig)))
    return events


def _run():
    return child_run.run_child(["unused"], b"", timeout=1, output_cap=10)


def test_process_group_cleanup_uses_sigkill(monkeypatch):
    events = _setup(monkeypatch)
    _run()
    assert ("kill", signal.SIGKILL) in events


def test_stderr_is_discarded(monkeypatch):
    events = _setup(monkeypatch)
    _run()
    kwargs = next(value[1] for value in events if isinstance(value, tuple) and value[0] == "popen")
    assert kwargs["stderr"] is child_run.subprocess.DEVNULL


def test_process_group_is_killed_before_threads_join(monkeypatch):
    events = _setup(monkeypatch)
    _run()
    first_kill = next(
        i for i, event in enumerate(events) if isinstance(event, tuple) and event[0] == "kill"
    )
    first_join = events.index("join")
    assert first_kill < first_join


def test_live_reader_refuses_result(monkeypatch):
    _setup(monkeypatch, alive=("reader",))
    assert _run() == (b"", False, 1)


def test_live_writer_refuses_result(monkeypatch):
    _setup(monkeypatch, alive=("writer",))
    assert _run() == (b"", False, 1)


def test_finally_kills_after_thread_start_failure(monkeypatch):
    events = _setup(monkeypatch, fail_start=True)
    assert _run() == (b"", False, 1)
    assert any(isinstance(event, tuple) and event[0] == "kill" for event in events)
