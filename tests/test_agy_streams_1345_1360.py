"""Stream cleanup after a failed _stop: never closes under a live reader (#1345, #1360)."""

import threading
import time
from types import SimpleNamespace

import pytest

from cairnwork.workers import agy as agy_module
from cairnwork.workers import agy_streams
from cairnwork.workers.agy import AgyWorker
from cairnwork.workers.agy_dispatch import Dispatch


class _Stream:
    def __init__(self, name, calls, error=None):
        self.name, self.calls, self.error = name, calls, error

    def close(self):
        self.calls.append(self.name)
        if self.error is not None:
            raise self.error


def _reader():
    stop = threading.Event()
    thread = threading.Thread(target=stop.wait, daemon=True)
    thread.start()
    return thread, stop


def test_close_streams_reraises_the_first_of_several_errors():
    calls, a, b = [], OSError("a"), OSError("b")
    process = SimpleNamespace(
        stdin=_Stream("i", calls, a), stdout=_Stream("o", calls), stderr=_Stream("e", calls, b)
    )
    with pytest.raises(OSError, match="a") as caught:
        agy_streams.close_streams(process, _Stream("log", calls))
    assert caught.value is a
    assert calls == ["i", "o", "e", "log"]


@pytest.mark.parametrize("who", ["copier", "reader"])
def test_fail_cleanup_leaves_streams_open_while_a_thread_reads(who):
    calls, (thread, stop) = [], _reader()
    process = SimpleNamespace(
        stdin=_Stream("i", calls), stdout=_Stream("o", calls), stderr=_Stream("e", calls)
    )
    threads = (thread, None) if who == "copier" else (None, thread)
    primary, failure = ValueError("start"), OSError("stop")
    started = time.monotonic()
    try:
        with pytest.raises(ValueError, match="start") as caught:
            agy_streams.fail_cleanup(primary, failure, process, _Stream("log", calls), threads)
    finally:
        stop.set()
    assert time.monotonic() - started < 2
    assert calls == []  # nothing closed under the reader
    assert caught.value.__cause__ is failure


def test_fail_cleanup_closes_when_no_thread_reads():
    calls = []
    process = SimpleNamespace(stdin=None, stdout=_Stream("o", calls), stderr=None)
    with pytest.raises(OSError, match="stop"):
        failure = OSError("stop")
        agy_streams.fail_cleanup(failure, failure, process, _Stream("log", calls), (None, None))
    assert calls == ["o", "log"]


def _await_process(wait):
    stream = SimpleNamespace(close=lambda: None)
    return SimpleNamespace(returncode=0, stdin=stream, stdout=stream, stderr=stream, wait=wait)


def test_await_stop_interrupt_propagates(monkeypatch):
    def stop(*_args):
        raise KeyboardInterrupt

    monkeypatch.setattr(agy_module, "_stop", stop)
    worker = AgyWorker(timeout_seconds=1, dispatch=Dispatch(path="d"))
    with pytest.raises(KeyboardInterrupt):
        worker._await(_await_process(lambda **_k: 0), None, None, ("a", "m", ()), None, "log")


def test_await_does_not_hang_when_copier_reads_and_stop_fails(monkeypatch):
    thread, stop = _reader()

    def stop_fails(*_args):
        raise OSError("stop failed")

    monkeypatch.setattr(agy_module, "_stop", stop_fails)
    process = _await_process(lambda **_k: 0)
    process.stdout = _Stream("o", calls := [])
    started = time.monotonic()
    try:
        with pytest.raises(OSError, match="stop failed"):
            AgyWorker(timeout_seconds=1)._await(process, thread, None, ("a", "m", ()), None, "log")
    finally:
        stop.set()
    assert time.monotonic() - started < 2
    assert calls == []


def test_start_failure_stop_interrupt_propagates(tmp_path, monkeypatch):
    binary = tmp_path / "agy"
    binary.write_text(f"#!{__import__('sys').executable}\npass\n")
    binary.chmod(0o755)
    workdir = tmp_path / "attempt-stop-interrupt"
    workdir.mkdir()

    def fail_start(_self):
        raise RuntimeError("start failed")

    def stop(process, *_args):
        process.kill()
        process.wait()
        raise KeyboardInterrupt

    monkeypatch.setattr(threading.Thread, "start", fail_start)
    monkeypatch.setattr(agy_module, "_stop", stop)
    task = SimpleNamespace(title="t", instructions="i", allowed_files=("a.py",))
    with pytest.raises(KeyboardInterrupt):
        AgyWorker(agy=str(binary)).run(workdir, task)
