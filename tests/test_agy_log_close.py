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


def test_log_and_pipes_are_closed_when_thread_start_fails(tmp_path, monkeypatch):
    binary = tmp_path / "agy"
    binary.write_text(f"#!{sys.executable}\nimport time\ntime.sleep(5)\n")
    binary.chmod(0o755)
    workdir = tmp_path / f"attempt-{uuid.uuid4().hex[:8]}"
    workdir.mkdir()
    opened, procs = [], []
    real_fdopen = os.fdopen
    real_popen = agy_module.subprocess.Popen

    def capture_fdopen(*args, **kwargs):
        log = real_fdopen(*args, **kwargs)
        opened.append(log)
        return log

    def capture_popen(*args, **kwargs):
        proc = real_popen(*args, **kwargs)
        if args and str(binary) in str(args[0]):
            procs.append(proc)
        return proc

    def fail_start(self):
        raise RuntimeError("cannot start thread")

    monkeypatch.setattr(agy_module.os, "fdopen", capture_fdopen)
    monkeypatch.setattr(agy_module.subprocess, "Popen", capture_popen)
    monkeypatch.setattr(agy_module.threading.Thread, "start", fail_start)
    task = SimpleNamespace(title="t", instructions="i", allowed_files=("a.py",))

    with pytest.raises(RuntimeError, match="cannot start thread"):
        AgyWorker(agy=str(binary)).run(workdir, task)

    assert len(opened) == 1 and opened[0].closed
    assert len(procs) == 1 and procs[0].stdout.closed
    assert procs[0].returncode is not None and procs[0].returncode < 0


def test_dispatch_log_and_all_pipes_closed_when_reader_start_fails(tmp_path, monkeypatch):
    binary = tmp_path / "agy"
    binary.write_text(f"#!{sys.executable}\nimport time\ntime.sleep(5)\n")
    binary.chmod(0o755)
    workdir = tmp_path / f"attempt-{uuid.uuid4().hex[:8]}"
    workdir.mkdir()
    opened, procs = [], []
    real_fdopen = os.fdopen
    real_popen = agy_module.subprocess.Popen

    def capture_fdopen(*args, **kwargs):
        log = real_fdopen(*args, **kwargs)
        opened.append(log)
        return log

    def capture_popen(*args, **kwargs):
        proc = real_popen(*args, **kwargs)
        if args and str(binary) in str(args[0]):
            procs.append(proc)
        return proc

    def fail_start(self):
        raise RuntimeError("cannot start reader")

    monkeypatch.setattr(agy_module.os, "fdopen", capture_fdopen)
    monkeypatch.setattr(agy_module.subprocess, "Popen", capture_popen)
    monkeypatch.setattr(agy_module.agy_dispatch.StatusReader, "start", fail_start)
    task = SimpleNamespace(title="t", instructions="i", allowed_files=("a.py",))
    dispatch = agy_module.agy_dispatch.Dispatch(path=str(binary))

    with pytest.raises(RuntimeError, match="cannot start reader"):
        AgyWorker(agy=str(binary), dispatch=dispatch).run(workdir, task)

    assert len(opened) == 1 and opened[0].closed
    assert len(procs) == 1
    assert procs[0].stdin.closed and procs[0].stdout.closed and procs[0].stderr.closed
    assert procs[0].returncode is not None and procs[0].returncode < 0


@pytest.mark.parametrize("fail_name", ["_feed", "copy_capped"])
def test_dispatch_feed_or_copier_start_failure_stops_and_closes(tmp_path, monkeypatch, fail_name):
    binary = tmp_path / "agy"
    binary.write_text(f"#!{sys.executable}\nimport time\ntime.sleep(5)\n")
    binary.chmod(0o755)
    workdir = tmp_path / f"attempt-{uuid.uuid4().hex[:8]}"
    workdir.mkdir()
    opened, procs = [], []
    real_fdopen = os.fdopen
    real_popen = agy_module.subprocess.Popen
    real_start = agy_module.threading.Thread.start
    fail_target = getattr(agy_module, fail_name)

    def capture_fdopen(*args, **kwargs):
        log = real_fdopen(*args, **kwargs)
        opened.append(log)
        return log

    def capture_popen(*args, **kwargs):
        proc = real_popen(*args, **kwargs)
        if args and str(binary) in str(args[0]):
            procs.append(proc)
        return proc

    def maybe_fail_start(self):
        if getattr(self, "_target", None) is fail_target:
            raise RuntimeError(f"cannot start {fail_name}")
        return real_start(self)

    monkeypatch.setattr(agy_module.os, "fdopen", capture_fdopen)
    monkeypatch.setattr(agy_module.subprocess, "Popen", capture_popen)
    monkeypatch.setattr(agy_module.threading.Thread, "start", maybe_fail_start)
    task = SimpleNamespace(title="t", instructions="i", allowed_files=("a.py",))
    dispatch = agy_module.agy_dispatch.Dispatch(path=str(binary))

    with pytest.raises(RuntimeError, match=f"cannot start {fail_name}"):
        AgyWorker(agy=str(binary), dispatch=dispatch).run(workdir, task)

    assert len(opened) == 1 and opened[0].closed
    assert len(procs) == 1
    assert procs[0].stdin.closed and procs[0].stdout.closed and procs[0].stderr.closed
    assert procs[0].returncode is not None and procs[0].returncode < 0


def test_start_failure_while_held_leaves_streams_open(tmp_path, monkeypatch):
    binary = tmp_path / "agy"
    binary.write_text(f"#!{sys.executable}\nimport time\ntime.sleep(5)\n")
    binary.chmod(0o755)
    workdir = tmp_path / f"attempt-{uuid.uuid4().hex[:8]}"
    workdir.mkdir()
    opened, procs = [], []
    real_fdopen = os.fdopen
    real_popen = agy_module.subprocess.Popen

    def capture_fdopen(*args, **kwargs):
        log = real_fdopen(*args, **kwargs)
        opened.append(log)
        return log

    def capture_popen(*args, **kwargs):
        proc = real_popen(*args, **kwargs)
        if args and str(binary) in str(args[0]):
            procs.append(proc)
        return proc

    def fail_start(self):
        raise RuntimeError("cannot start thread")

    monkeypatch.setattr(agy_module.os, "fdopen", capture_fdopen)
    monkeypatch.setattr(agy_module.subprocess, "Popen", capture_popen)
    monkeypatch.setattr(agy_module.threading.Thread, "start", fail_start)
    monkeypatch.setattr(agy_module, "_stop", lambda *_a, **_k: (True, "", None))
    task = SimpleNamespace(title="t", instructions="i", allowed_files=("a.py",))

    try:
        with pytest.raises(RuntimeError, match="cannot start thread"):
            AgyWorker(agy=str(binary)).run(workdir, task)
        assert len(opened) == 1 and not opened[0].closed
        assert len(procs) == 1 and not procs[0].stdout.closed
    finally:
        for proc in procs:
            proc.kill()
            proc.wait()
        for log in opened:
            if not log.closed:
                log.close()
