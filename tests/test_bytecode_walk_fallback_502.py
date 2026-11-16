"""The no-nofollow-chmod fallback (Linux) chmods through an O_PATH descriptor, or fails closed."""

import os
import stat
import sys

import pytest

from cairnwork import bytecode_walk

LINUX = sys.platform.startswith("linux") and hasattr(os, "O_PATH")


def closed_tree(tmp_path):
    mid, outside = tmp_path / "mid", tmp_path / "outside"
    (mid / "closed").mkdir(parents=True)
    (outside / "closed").mkdir(parents=True)
    (mid / "closed").chmod(0)
    (outside / "closed").chmod(0)
    return mid, outside


def no_nofollow(monkeypatch, fail_proc=None):
    real, calls = os.chmod, []

    def chmod(path, mode, *args, **kwargs):
        if kwargs.get("follow_symlinks", True) is False:
            raise NotImplementedError("no nofollow chmod")
        calls.append(path)
        if fail_proc and isinstance(path, str) and path.startswith("/proc/self/fd/"):
            raise fail_proc("no /proc")
        return real(path, mode, *args, **kwargs)

    monkeypatch.setattr(os, "chmod", chmod)
    return calls


@pytest.mark.skipif(not LINUX, reason="needs Linux O_PATH")
def test_a_swap_between_the_identity_check_and_chmod_changes_nothing_outside(tmp_path, monkeypatch):
    mid, outside = closed_tree(tmp_path)
    real_chmod, swapped = os.chmod, []

    def swap_then_chmod(path, mode, *args, **kwargs):
        if kwargs.get("follow_symlinks", True) is False:
            raise NotImplementedError("no nofollow chmod")
        if not swapped:  # after the identity check, immediately before the real chmod
            swapped.append(1)
            (mid / "closed").rename(tmp_path / "closed.real")
            (mid / "closed").symlink_to(outside / "closed")
        return real_chmod(path, mode, *args, **kwargs)

    monkeypatch.setattr(os, "chmod", swap_then_chmod)
    try:
        bytecode_walk.opened(str(mid / "closed"))
    except OSError:
        pass
    assert swapped
    assert stat.S_IMODE((outside / "closed").stat().st_mode) == 0


@pytest.mark.parametrize("exc", [FileNotFoundError, NotImplementedError])
def test_no_proc_fd_fails_closed_without_a_path_chmod(tmp_path, monkeypatch, exc):
    if not hasattr(os, "O_PATH"):
        monkeypatch.setattr(os, "O_PATH", os.O_RDONLY, raising=False)
    mid, _ = closed_tree(tmp_path)
    calls = no_nofollow(monkeypatch, fail_proc=exc)
    with pytest.raises(OSError):
        bytecode_walk.opened(str(mid / "closed"))
    assert all(c.startswith("/proc/self/fd/") for c in calls)
    assert stat.S_IMODE((mid / "closed").stat().st_mode) == 0


def test_a_missing_o_path_fails_closed_without_any_chmod(tmp_path, monkeypatch):
    monkeypatch.delattr(os, "O_PATH", raising=False)
    mid, _ = closed_tree(tmp_path)
    calls = no_nofollow(monkeypatch)
    with pytest.raises(OSError):
        bytecode_walk.opened(str(mid / "closed"))
    assert calls == []
    assert stat.S_IMODE((mid / "closed").stat().st_mode) == 0
