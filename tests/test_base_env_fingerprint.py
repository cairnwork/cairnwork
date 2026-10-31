"""#234, review of #258: an interpreter fingerprint that cannot be read keeps nothing. Every
dispatch then probes, and no pass marker is read or written. pyvenv.cfg is read only when it is
a regular file, without blocking. Only ``_probe`` is faked."""

import os
import threading

import pytest
from test_base_env import KEY_TASK, _venv

from cairnwork import p2p
from cairnwork.pipeline import TaskResult
from cairnwork.verify_context import VerifyContext


@pytest.fixture
def check(monkeypatch, tmp_path):
    """check(python): one base_env_check with that verifier Python; returns the probes so far."""
    probed: list = []
    monkeypatch.setattr(p2p, "_base_tests", lambda task, exclude: ("tests/test_calc.py",))
    monkeypatch.setattr(p2p, "_probe", lambda *a, **k: probed.append(a) or ({"t": "passed"}, ""))

    def run(python):
        result = TaskResult("t", "a" + "0" * 16, "FAILED")
        ctx = VerifyContext.of(KEY_TASK, tmp_path / "runs", result.attempt_id, python=python)
        p2p.base_env_check(KEY_TASK, result, ctx)
        return len(probed)

    return run


def markers(tmp_path):
    root = tmp_path / "runs" / "base-env-ok"
    return sorted(root.iterdir()) if root.is_dir() else []


@pytest.fixture
def restore_modes():
    changed: list = []
    yield changed
    for path, mode in changed:
        os.chmod(path, mode)


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads any file")
@pytest.mark.parametrize("part", ["pyvenv.cfg", "lib", "site-packages"])
def test_an_unreadable_part_probes_every_time_and_keeps_no_marker(
    check, tmp_path, restore_modes, part
):
    python = _venv(tmp_path / "venv")
    lib = tmp_path / "venv" / "lib"
    path = {"pyvenv.cfg": tmp_path / "venv" / "pyvenv.cfg", "lib": lib}.get(
        part, lib / "python3.12"
    )
    restore_modes.append((path, path.stat().st_mode))
    os.chmod(path, 0)
    assert p2p._env_fingerprint(python) is None
    assert check(python) == 1 and check(python) == 2  # not remembered
    assert markers(tmp_path) == []  # and nothing written for that key


def test_a_missing_interpreter_is_unreadable(check, tmp_path):
    python = str(tmp_path / "nowhere" / "bin" / "python")
    assert p2p._env_fingerprint(python) is None
    assert check(python) == 1 and check(python) == 2 and markers(tmp_path) == []


def test_a_readable_venv_is_remembered(check, tmp_path):
    python = _venv(tmp_path / "venv")
    assert check(python) == 1 and check(python) == 1 and len(markers(tmp_path)) == 1


def test_a_venv_without_pyvenv_cfg_is_still_remembered(check, tmp_path):
    python = _venv(tmp_path / "venv")
    (tmp_path / "venv" / "pyvenv.cfg").unlink()  # e.g. a system interpreter: absent is a fact
    assert check(python) == 1 and check(python) == 1


@pytest.mark.parametrize("kind", ["fifo", "symlink", "dangling-symlink", "directory"])
def test_a_pyvenv_cfg_that_is_not_a_regular_file_is_unreadable(check, tmp_path, kind):
    python = _venv(tmp_path / "venv")
    cfg = tmp_path / "venv" / "pyvenv.cfg"
    cfg.unlink()
    if kind == "fifo":
        if not hasattr(os, "mkfifo"):
            pytest.skip("no mkfifo on this platform")
        os.mkfifo(cfg)
    elif kind == "symlink":
        (tmp_path / "real.cfg").write_text("version = 3.12.1\n")
        cfg.symlink_to(tmp_path / "real.cfg")
    elif kind == "dangling-symlink":  # not "absent": a link is there
        cfg.symlink_to(tmp_path / "nonexistent")
    else:
        cfg.mkdir()
    done: list = []
    worker = threading.Thread(target=lambda: done.append(check(python) + check(python)))
    worker.daemon = True  # a blocking open would hang here, not the whole run
    worker.start()
    worker.join(10)
    assert done == [3], "the fingerprint blocked or was remembered"  # probes 1, then 2
    assert markers(tmp_path) == []


def test_a_fifo_swapped_in_after_the_type_check_neither_blocks_nor_counts(tmp_path, monkeypatch):
    if not hasattr(os, "mkfifo"):
        pytest.skip("no mkfifo on this platform")
    regular = tmp_path / "regular"
    regular.write_text("version = 3.12.1\n")
    fifo = tmp_path / "pyvenv.cfg"
    os.mkfifo(fifo)
    real_lstat = os.lstat
    monkeypatch.setattr(os, "lstat", lambda p: real_lstat(regular if p == str(fifo) else p))
    done: list = []

    def read():
        try:
            p2p._small_file(str(fifo))
        except p2p._Unreadable:
            done.append("unreadable")

    worker = threading.Thread(target=read, daemon=True)  # a blocking open would hang here
    worker.start()
    worker.join(10)
    assert done == ["unreadable"]


def test_a_pyvenv_cfg_that_cannot_be_looked_up_is_unreadable_not_absent(tmp_path, monkeypatch):
    python = _venv(tmp_path / "venv")
    cfg = str(tmp_path / "venv" / "pyvenv.cfg")
    real_lstat = os.lstat

    def lstat(path):
        if str(path) == cfg:
            raise PermissionError(13, "Permission denied", path)
        return real_lstat(path)

    monkeypatch.setattr(os, "lstat", lstat)
    assert p2p._env_fingerprint(python) is None


def test_the_metadata_is_that_of_the_file_actually_read(tmp_path, monkeypatch):
    cfg = tmp_path / "pyvenv.cfg"
    cfg.write_text("version = 3.12.1\n")
    other = tmp_path / "other"
    other.write_text("a different, longer file than the one read\n")
    real_lstat = os.lstat
    monkeypatch.setattr(os, "lstat", lambda p: real_lstat(other if p == str(cfg) else p))
    text, _, size = p2p._small_file(str(cfg))  # swapped after the lstat
    assert text == "version = 3.12.1\n" and size == cfg.stat().st_size
