"""Fallback-branch and descent races for grant_read (#1361)."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from cairnwork.verifier_account import VerifierAccount


def _swap_after_second_lstat(monkeypatch: pytest.MonkeyPatch, target: str, swap) -> list[int]:
    """Run ``swap`` right after the fallback branch's pre-check lstat of ``target`` returns.

    The listing loop does the first lstat; ``_chmod_unreadable`` does the second.
    The swap lands between that pre-check and the chmod (the window finding 1 names).
    """
    real_stat = os.stat
    seen: list[int] = []
    swapped: list[int] = []

    def stat_then_swap(path, *args, **kwargs):
        result = real_stat(path, *args, **kwargs)
        if (
            Path(str(path)).name == target
            and kwargs.get("dir_fd") is not None
            and kwargs.get("follow_symlinks") is False
        ):
            seen.append(1)
            if len(seen) == 2 and not swapped:
                swapped.append(1)
                swap()
        return result

    monkeypatch.setattr(os, "stat", stat_then_swap)
    return swapped


@pytest.mark.skipif(hasattr(os, "O_PATH"), reason="Linux uses the O_PATH branch")
def test_grant_read_fallback_refuses_mode_zero_file_swapped_before_chmod(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / "s"
    target.write_text("x\n", encoding="utf-8")
    target.chmod(0o600)
    root = tmp_path / "checkout"
    root.mkdir()
    victim = root / "f"
    victim.write_text("x\n", encoding="utf-8")
    victim.chmod(0)

    def swap() -> None:
        victim.rename(root / "f.real")
        victim.symlink_to(target)

    swapped = _swap_after_second_lstat(monkeypatch, "f", swap)
    try:
        with pytest.raises(OSError):
            VerifierAccount.grant_read(root)
        assert swapped
        assert stat.S_IMODE(target.stat().st_mode) == 0o600
    finally:
        monkeypatch.undo()
        if (root / "f.real").exists():
            (root / "f.real").chmod(0o600)


@pytest.mark.skipif(hasattr(os, "O_PATH"), reason="Linux uses the O_PATH branch")
def test_grant_read_fallback_refuses_mode_zero_dir_swapped_before_chmod(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    root = tmp_path / "checkout"
    closed = root / "closed"
    closed.mkdir(parents=True)
    closed.chmod(0)

    def swap() -> None:
        closed.rename(root / "closed.real")
        closed.symlink_to(outside)

    swapped = _swap_after_second_lstat(monkeypatch, "closed", swap)
    try:
        with pytest.raises(OSError):
            VerifierAccount.grant_read(root)
        assert swapped
        assert stat.S_IMODE(outside.stat().st_mode) == 0o700
    finally:
        monkeypatch.undo()
        if (root / "closed.real").exists():
            (root / "closed.real").chmod(0o700)


def test_grant_read_in_tree_symlink_widens_only_the_link(tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("x\n", encoding="utf-8")
    outside.chmod(0o600)
    root = tmp_path / "checkout"
    root.mkdir()
    (root / "link").symlink_to(outside)
    VerifierAccount.grant_read(root)
    assert stat.S_IMODE(outside.stat().st_mode) == 0o600


def test_grant_read_refuses_directory_replaced_before_descent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "checkout"
    (root / "d").mkdir(parents=True)
    victim = tmp_path / "other.txt"
    victim.write_text("x\n", encoding="utf-8")
    victim.chmod(0o600)
    real_open = os.open
    opens: list[int] = []

    def open_then_replace(path, *args, **kwargs):
        if Path(str(path)).name == "d" and kwargs.get("dir_fd") is not None:
            opens.append(1)
            if len(opens) == 2:  # the descent reopen, after the listing pass opened it once
                (root / "d").rename(root / "d.real")
                (root / "d").mkdir()
                (root / "d" / "f").write_text("x\n", encoding="utf-8")
                (root / "d" / "f").chmod(0o600)
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", open_then_replace)
    with pytest.raises(OSError):
        VerifierAccount.grant_read(root)
    assert len(opens) >= 2
    assert stat.S_IMODE((root / "d" / "f").stat().st_mode) == 0o600


@pytest.mark.skipif(not hasattr(os, "O_PATH"), reason="O_PATH branch is Linux-only")
@pytest.mark.skipif(os.geteuid() == 0, reason="root can open mode-000 entries directly")
def test_grant_read_linux_o_path_branch_is_taken_for_mode_zero_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "checkout"
    closed = root / "closed"
    closed.mkdir(parents=True)
    inner = closed / "inner.txt"
    inner.write_text("x\n", encoding="utf-8")
    inner.chmod(0o600)
    closed.chmod(0)
    real_chmod = os.chmod
    via_proc: list[tuple[str, int]] = []

    def spy_chmod(path, mode, *args, **kwargs):
        if isinstance(path, str) and path.startswith("/proc/self/fd/"):
            via_proc.append((path, mode))
        return real_chmod(path, mode, *args, **kwargs)

    monkeypatch.setattr(os, "chmod", spy_chmod)
    try:
        VerifierAccount.grant_read(root)
        assert via_proc, "the O_PATH fallback was not used for the mode-000 directory"
        assert via_proc[0][1] & 0o555 == 0o555  # owner and group/other read+traverse
        assert stat.S_IMODE(closed.stat().st_mode) == 0o555
        assert stat.S_IMODE(inner.stat().st_mode) == 0o644  # walked inside it
    finally:
        monkeypatch.undo()
        closed.chmod(0o700)
