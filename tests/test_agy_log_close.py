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


def test_log_is_closed_when_setup_before_popen_fails(tmp_path, monkeypatch):
    binary = tmp_path / "agy"
    binary.write_text(f"#!{sys.executable}\npass\n")
    binary.chmod(0o755)
    workdir = tmp_path / f"attempt-{uuid.uuid4().hex[:8]}"
    workdir.mkdir()
    opened = []
    real_fdopen = os.fdopen

    def capture_fdopen(*args, **kwargs):
        log = real_fdopen(*args, **kwargs)
        opened.append(log)
        return log

    def fail_mark():
        raise OSError("no boot mark")

    monkeypatch.setattr(agy_module.os, "fdopen", capture_fdopen)
    monkeypatch.setattr(agy_module.proc_start, "boot_mark", fail_mark)
    task = SimpleNamespace(title="t", instructions="i", allowed_files=("a.py",))

    with pytest.raises(OSError, match="no boot mark"):
        AgyWorker(agy=str(binary)).run(workdir, task)

    assert len(opened) == 1
    assert opened[0].closed


def test_raw_fd_is_closed_when_fdopen_fails(tmp_path, monkeypatch):
    binary = tmp_path / "agy"
    binary.write_text(f"#!{sys.executable}\npass\n")
    binary.chmod(0o755)
    workdir = tmp_path / f"attempt-{uuid.uuid4().hex[:8]}"
    workdir.mkdir()
    fds = []
    real_open = os.open

    def capture_open(*args, **kwargs):
        fd = real_open(*args, **kwargs)
        fds.append(fd)
        return fd

    def fail_fdopen(*_a, **_k):
        raise MemoryError("no file object")

    monkeypatch.setattr(agy_module.os, "open", capture_open)
    monkeypatch.setattr(agy_module.os, "fdopen", fail_fdopen)
    task = SimpleNamespace(title="t", instructions="i", allowed_files=("a.py",))
    with pytest.raises(MemoryError):
        AgyWorker(agy=str(binary)).run(workdir, task)
    with pytest.raises(OSError):
        os.fstat(fds[0])


def test_no_fd_is_left_open_when_env_setup_fails(tmp_path, monkeypatch):
    binary = tmp_path / "agy"
    binary.write_text(f"#!{sys.executable}\npass\n")
    binary.chmod(0o755)
    workdir = tmp_path / f"attempt-{uuid.uuid4().hex[:8]}"
    workdir.mkdir()
    fds = []
    real_open = os.open

    def capture_open(*args, **kwargs):
        fd = real_open(*args, **kwargs)
        fds.append(fd)
        return fd

    def fail_env():
        raise MemoryError("env")

    monkeypatch.setattr(agy_module.os, "open", capture_open)
    monkeypatch.setattr(agy_module, "_kept_env", fail_env)
    task = SimpleNamespace(title="t", instructions="i", allowed_files=("a.py",))
    with pytest.raises(MemoryError):
        AgyWorker(agy=str(binary)).run(workdir, task)
    for fd in fds:
        with pytest.raises(OSError):
            os.fstat(fd)
