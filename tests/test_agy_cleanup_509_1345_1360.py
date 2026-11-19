"""Regression tests for Agy wait bounds and cleanup failures."""

import threading
from types import SimpleNamespace

import pytest

from cairnwork.workers import agy as agy_module
from cairnwork.workers.agy import AgyWorker
from cairnwork.workers.agy_dispatch import Dispatch


@pytest.mark.parametrize("pool_wait, expected", [(None, 160), (37, 197)])
def test_await_wait_includes_dispatch_pool_wait(monkeypatch, pool_wait, expected):
    dispatch = Dispatch(path="dispatcher", pool_wait_seconds=pool_wait) if pool_wait else None
    worker = AgyWorker(timeout_seconds=100, dispatch=dispatch)
    stream = SimpleNamespace(close=lambda: None)
    process = SimpleNamespace(
        returncode=0,
        stdin=stream,
        stdout=stream,
        stderr=stream,
        wait=lambda **kwargs: seen.update(kwargs),
    )
    seen = {}
    monkeypatch.setattr(agy_module, "_stop", lambda *_args: (False, SimpleNamespace(gone=True), 0))
    monkeypatch.setattr(AgyWorker, "_fence", lambda *_args: None)

    worker._await(process, None, None, ("attempt", "mark", ()), None, "log")

    assert seen == {"timeout": expected}


def test_start_failure_keeps_original_exception_and_closes_exited_process(tmp_path, monkeypatch):
    binary = tmp_path / "agy"
    binary.write_text(f"#!{__import__('sys').executable}\npass\n")
    binary.chmod(0o755)
    workdir = tmp_path / "attempt-cleanup"
    workdir.mkdir()
    opened = []
    real_fdopen = agy_module.os.fdopen

    def capture_fdopen(*args, **kwargs):
        log = real_fdopen(*args, **kwargs)
        opened.append(log)
        return log

    def fail_start(_self):
        raise RuntimeError("original start error")

    def fail_stop(process, *_args):
        process.kill()
        process.wait()
        raise OSError("cleanup failed")

    monkeypatch.setattr(agy_module.os, "fdopen", capture_fdopen)
    monkeypatch.setattr(threading.Thread, "start", fail_start)
    monkeypatch.setattr(agy_module, "_stop", fail_stop)
    task = SimpleNamespace(title="t", instructions="i", allowed_files=("a.py",))

    with pytest.raises(RuntimeError, match="original start error") as caught:
        AgyWorker(agy=str(binary)).run(workdir, task)

    assert isinstance(caught.value.__context__, OSError)
    assert opened[0].closed


def test_close_streams_attempts_all_and_reraises_first_error():
    calls = []

    class Stream:
        def __init__(self, name, error=None):
            self.name = name
            self.error = error

        def close(self):
            calls.append(self.name)
            if self.error:
                raise self.error

    first = OSError("first")
    process = SimpleNamespace(
        stdin=Stream("stdin", first), stdout=Stream("stdout"), stderr=Stream("stderr")
    )

    with pytest.raises(OSError, match="first") as caught:
        agy_module._close_streams(process, Stream("log"))

    assert caught.value is first
    assert calls == ["stdin", "stdout", "stderr", "log"]
