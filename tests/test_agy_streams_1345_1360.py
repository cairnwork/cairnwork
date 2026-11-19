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


def _run_start_failure(tmp_path, monkeypatch, *, dispatch, fail_target, kill_in_stop):
    """AgyWorker.run with a failing thread start and a failing _stop; returns what happened."""
    import os
    import sys

    binary = tmp_path / "agy"
    binary.write_text(f"#!{sys.executable}\nimport time\ntime.sleep(8)\n")
    binary.chmod(0o755)
    workdir = tmp_path / "attempt-reader"
    workdir.mkdir()
    opened, procs = [], []
    real_fdopen, real_popen = os.fdopen, agy_module.subprocess.Popen
    real_start = threading.Thread.start

    def capture_fdopen(*args, **kwargs):
        opened.append(real_fdopen(*args, **kwargs))
        return opened[-1]

    def capture_popen(*args, **kwargs):
        procs.append(real_popen(*args, **kwargs))
        return procs[-1]

    def maybe_fail(self):
        if getattr(self, "_target", None) is fail_target():
            raise RuntimeError("start failed")
        return real_start(self)

    def stop_fails(process, *_args):
        if kill_in_stop:
            process.kill()
            process.wait()
        raise OSError("stop failed")

    monkeypatch.setattr(agy_module.os, "fdopen", capture_fdopen)
    monkeypatch.setattr(agy_module.subprocess, "Popen", capture_popen)
    monkeypatch.setattr(threading.Thread, "start", maybe_fail)
    monkeypatch.setattr(agy_module, "_stop", stop_fails)
    task = SimpleNamespace(title="t", instructions="i", allowed_files=("a.py",))
    outcome = []

    def go():
        try:
            AgyWorker(agy=str(binary), dispatch=dispatch).run(workdir, task)
        except BaseException as exc:  # noqa: BLE001 - what run() raised is the result
            outcome.append(exc)

    runner = threading.Thread(target=go, daemon=True)
    runner.start()
    runner.join(timeout=3)  # a close under a live reader would hang here
    hung = runner.is_alive()
    for proc in procs:
        proc.kill()
    return outcome, opened, procs, hung


def test_dispatcher_start_failure_with_live_status_reader_does_not_close_or_hang(
    tmp_path, monkeypatch
):
    outcome, opened, procs, hung = _run_start_failure(
        tmp_path,
        monkeypatch,
        dispatch=Dispatch(path=str(tmp_path / "agy")),
        fail_target=lambda: agy_module._feed,
        kill_in_stop=False,  # the dispatcher still holds stderr: the StatusReader is reading
    )
    assert not hung
    assert isinstance(outcome[0], RuntimeError) and "start failed" in str(outcome[0])
    assert not procs[0].stderr.closed and not opened[0].closed


def test_start_failure_with_no_reader_alive_still_closes_streams(tmp_path, monkeypatch):
    outcome, opened, procs, hung = _run_start_failure(
        tmp_path,
        monkeypatch,
        dispatch=None,
        fail_target=lambda: agy_module.copy_capped,
        kill_in_stop=True,  # nothing reads any more: the streams must be closed
    )
    assert not hung
    assert isinstance(outcome[0], RuntimeError)
    assert procs[0].stdout.closed and opened[0].closed


@pytest.mark.parametrize("reader_alive", [True, False])
def test_await_with_only_the_reader_alive(monkeypatch, reader_alive):
    thread, stop = _reader()
    if not reader_alive:
        stop.set()
        thread.join()

    def stop_fails(*_args):
        raise OSError("stop failed")

    monkeypatch.setattr(agy_module, "_stop", stop_fails)
    calls = []
    process = _await_process(lambda **_k: 0)
    process.stderr = _Stream("e", calls)
    try:
        with pytest.raises(OSError, match="stop failed"):
            AgyWorker(timeout_seconds=1)._await(process, None, thread, ("a", "m", ()), None, "log")
    finally:
        stop.set()
    assert calls == ([] if reader_alive else ["e"])
