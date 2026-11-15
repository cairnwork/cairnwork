"""A failed process launch closes the worker's log file."""

import os
import sys
import uuid
from types import SimpleNamespace

import pytest

from cairnwork.workers import agy as agy_module
from cairnwork.workers.agy import AgyWorker


def test_log_is_closed_when_popen_fails(tmp_path, monkeypatch):
    run = uuid.uuid4().hex[:8]
    binary = tmp_path / "agy"
    binary.write_text(f"#!{sys.executable}\npass\n")
    binary.chmod(0o755)
    workdir = tmp_path / f"attempt-{run}"
    workdir.mkdir()
    opened = []
    real_fdopen = os.fdopen

    def capture_fdopen(*args, **kwargs):
        log = real_fdopen(*args, **kwargs)
        opened.append(log)
        return log

    def fail_popen(*_args, **_kwargs):
        raise OSError("launch failed")

    monkeypatch.setattr(agy_module.os, "fdopen", capture_fdopen)
    monkeypatch.setattr(agy_module.subprocess, "Popen", fail_popen)
    task = SimpleNamespace(title="t", instructions="i", allowed_files=("a.py",))

    with pytest.raises(OSError, match="launch failed"):
        AgyWorker(agy=str(binary)).run(workdir, task)

    assert len(opened) == 1
    assert opened[0].closed
