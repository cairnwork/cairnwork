"""#995 follow-up for deny_sweep: the try limit, and an owner whose state cannot be read."""

import os
import time

import pytest

from cairnwork import deny_sweep


def _stale(path, pid=None, age=7200.0):
    path.mkdir()
    old = time.time() - age
    if pid is not None:
        (path / "owner.pid").write_text(f"{pid}\n")
        os.utime(path / "owner.pid", (old, old))
    os.utime(path, (old, old))


class _Ordered:
    """os.scandir of the temp dir only, in a fixed name order; children pass through."""

    def __init__(self, real, top, reverse):
        self.real, self.top, self.reverse = real, str(top), reverse

    def __call__(self, path):
        if str(path) != self.top:
            return self.real(path)
        entries = sorted(self.real(path), key=lambda e: e.name, reverse=self.reverse)
        return _Listing(entries)


class _Listing:
    def __init__(self, entries):
        self.entries = entries

    def __enter__(self):
        return iter(self.entries)

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        return iter(self.entries)


@pytest.mark.parametrize("reverse", [False, True])
def test_a_kept_directory_does_not_use_up_the_try_limit(monkeypatch, tmp_path, reverse):
    for i in range(4):
        _stale(tmp_path / f"cairnwork-deny.a{i}", pid=os.getpid())
    dead = tmp_path / "cairnwork-deny.m-dead"
    _stale(dead, pid=None)
    monkeypatch.setattr(deny_sweep.os, "scandir", _Ordered(os.scandir, tmp_path, reverse))
    assert deny_sweep.sweep_stale(tmp_path, time.time(), max_remove=1) == 1
    assert not dead.exists()
    assert len(list(tmp_path.glob("cairnwork-deny.a*"))) == 4


def test_an_owner_that_cannot_be_checked_keeps_the_directory(monkeypatch, tmp_path):
    stale = tmp_path / "cairnwork-deny.unknown"
    _stale(stale, pid=4242)

    def fail(pid, sig):
        raise OSError("not permitted to say")

    monkeypatch.setattr(deny_sweep.os, "kill", fail)
    assert deny_sweep.sweep_stale(tmp_path, time.time()) == 0
    assert stale.exists()


def test_the_limit_counts_only_stale_directories(tmp_path):
    for i in range(3):
        _stale(tmp_path / f"cairnwork-deny.new{i}", age=10.0)
    for i in range(2):
        _stale(tmp_path / f"cairnwork-deny.old{i}")
    assert deny_sweep.sweep_stale(tmp_path, time.time(), max_remove=2) == 2
    assert len(list(tmp_path.glob("cairnwork-deny.new*"))) == 3
    assert not list(tmp_path.glob("cairnwork-deny.old*"))


def test_a_limit_of_zero_removes_nothing(tmp_path):
    _stale(tmp_path / "cairnwork-deny.old")
    assert deny_sweep.sweep_stale(tmp_path, time.time(), max_remove=0) == 0
    assert (tmp_path / "cairnwork-deny.old").exists()
