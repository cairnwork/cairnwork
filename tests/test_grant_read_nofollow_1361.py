"""Issue #1361: handle-based O_NOFOLLOW/fchmod in grant_read, blackbox_runner and COPY_HELPER."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from cairnwork import blackbox_runner
from cairnwork.account_checkout import COPY_HELPER
from cairnwork.verifier_account import VerifierAccount


def test_grant_read_refuses_symlink_root(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    (outside / "secret.txt").write_text("x\n", encoding="utf-8")
    (outside / "secret.txt").chmod(0o600)
    root = tmp_path / "checkout"
    root.symlink_to(outside)

    with pytest.raises(OSError):
        VerifierAccount.grant_read(root)
    assert stat.S_IMODE(outside.stat().st_mode) == 0o700
    assert stat.S_IMODE((outside / "secret.txt").stat().st_mode) == 0o600


def test_grant_read_refuses_swapped_directory_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    root = tmp_path / "checkout"
    (root / "sub").mkdir(parents=True)
    (root / "sub").chmod(0o700)

    real_stat = os.stat
    swapped: list[int] = []

    def stat_then_swap(path, *args, **kwargs):
        info = real_stat(path, *args, **kwargs)
        if not swapped and Path(str(path)).name == "sub" and stat.S_ISDIR(info.st_mode):
            swapped.append(1)
            (root / "sub").rename(root / "sub.real")
            (root / "sub").symlink_to(outside)
        return info

    monkeypatch.setattr(os, "stat", stat_then_swap)
    with pytest.raises(OSError):
        VerifierAccount.grant_read(root)
    assert swapped
    assert stat.S_IMODE(outside.stat().st_mode) == 0o700


def test_grant_read_refuses_swapped_file_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("secret\n", encoding="utf-8")
    outside.chmod(0o600)
    root = tmp_path / "checkout"
    root.mkdir()
    target = root / "f.txt"
    target.write_text("x\n", encoding="utf-8")
    target.chmod(0o600)

    real_stat = os.stat
    swapped: list[int] = []

    def stat_then_swap(path, *args, **kwargs):
        info = real_stat(path, *args, **kwargs)
        if not swapped and Path(str(path)).name == "f.txt" and stat.S_ISREG(info.st_mode):
            swapped.append(1)
            target.rename(root / "f.txt.real")
            target.symlink_to(outside)
        return info

    monkeypatch.setattr(os, "stat", stat_then_swap)
    with pytest.raises(OSError):
        VerifierAccount.grant_read(root)
    assert swapped
    assert stat.S_IMODE(outside.stat().st_mode) == 0o600


def test_grant_read_refuses_file_swapped_to_same_inode_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outside = tmp_path / "outside_dir"
    outside.mkdir(mode=0o700)
    moved = outside / "moved.txt"
    root = tmp_path / "checkout"
    root.mkdir()
    target = root / "f.txt"
    target.write_text("secret\n", encoding="utf-8")
    target.chmod(0o600)

    real_stat = os.stat
    swapped: list[int] = []

    def stat_then_swap(path, *args, **kwargs):
        info = real_stat(path, *args, **kwargs)
        if not swapped and Path(str(path)).name == "f.txt" and stat.S_ISREG(info.st_mode):
            swapped.append(1)
            target.rename(moved)
            target.symlink_to(moved)
        return info

    monkeypatch.setattr(os, "stat", stat_then_swap)
    with pytest.raises(OSError):
        VerifierAccount.grant_read(root)
    assert swapped
    assert stat.S_IMODE(moved.stat().st_mode) == 0o600


def test_grant_read_refuses_swapped_middle_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outside = tmp_path / "outside"
    (outside / "inner").mkdir(parents=True)
    outside.chmod(0o700)
    (outside / "inner").chmod(0o700)

    root = tmp_path / "checkout"
    (root / "mid" / "inner").mkdir(parents=True)
    (root / "mid").chmod(0o700)
    (root / "mid" / "inner").chmod(0o700)
    mid_ino = (root / "mid").stat().st_ino

    real_fchmod = os.fchmod
    swapped: list[int] = []

    def fchmod_then_swap(fd: int, mode: int) -> None:
        real_fchmod(fd, mode)
        if not swapped and os.fstat(fd).st_ino == mid_ino:
            swapped.append(1)
            (root / "mid").rename(root / "mid.real")
            (root / "mid").symlink_to(outside)

    monkeypatch.setattr(os, "fchmod", fchmod_then_swap)
    with pytest.raises(OSError):
        VerifierAccount.grant_read(root)
    assert swapped
    assert stat.S_IMODE((outside / "inner").stat().st_mode) == 0o700


def test_grant_read_refuses_replaced_real_file_inode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "checkout"
    root.mkdir()
    target = root / "f.txt"
    target.write_text("old\n", encoding="utf-8")
    target.chmod(0o600)
    newcomer = root / "newcomer.txt"
    newcomer.write_text("new\n", encoding="utf-8")
    newcomer.chmod(0o600)

    real_stat = os.stat
    swapped: list[int] = []

    def stat_then_replace(path, *args, **kwargs):
        info = real_stat(path, *args, **kwargs)
        if not swapped and Path(str(path)).name == "f.txt" and stat.S_ISREG(info.st_mode):
            swapped.append(1)
            newcomer.rename(target)
        return info

    monkeypatch.setattr(os, "stat", stat_then_replace)
    with pytest.raises(OSError):
        VerifierAccount.grant_read(root)
    assert swapped
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_grant_read_widens_mode_zero_entries_safely(tmp_path: Path) -> None:
    root = tmp_path / "checkout"
    closed = root / "closed"
    closed.mkdir(parents=True)
    inner = closed / "inner.txt"
    inner.write_text("x\n", encoding="utf-8")
    inner.chmod(0o600)
    secret = root / "secret.txt"
    secret.write_text("x\n", encoding="utf-8")
    secret.chmod(0)
    closed.chmod(0)
    try:
        VerifierAccount.grant_read(root)
        # owner bits are added too: group/other bits alone do not help the owning process
        assert stat.S_IMODE(secret.stat().st_mode) == 0o444
        assert stat.S_IMODE(closed.stat().st_mode) == 0o555
        # and the walk went on inside the formerly closed directory
        assert stat.S_IMODE(inner.stat().st_mode) == 0o644
        assert inner.read_text(encoding="utf-8") == "x\n"
    finally:
        closed.chmod(0o700)
        secret.chmod(0o600)


def test_grant_read_refuses_swapped_mode_zero_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    root = tmp_path / "checkout"
    closed = root / "closed"
    closed.mkdir(parents=True)
    closed.chmod(0)

    real_open = os.open
    swapped: list[int] = []

    def open_then_swap(path, *args, **kwargs):
        try:
            return real_open(path, *args, **kwargs)
        except PermissionError:
            if not swapped and Path(str(path)).name == "closed":
                swapped.append(1)
                closed.rename(root / "closed.real")
                closed.symlink_to(outside)
            raise

    monkeypatch.setattr(os, "open", open_then_swap)
    try:
        with pytest.raises(OSError):
            VerifierAccount.grant_read(root)
        assert stat.S_IMODE(outside.stat().st_mode) == 0o700
    finally:
        if (root / "closed.real").exists():
            (root / "closed.real").chmod(0o700)
        elif closed.exists() and not closed.is_symlink():
            closed.chmod(0o700)


def test_blackbox_runner_uses_fchmod_not_path_chmod(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_chmod(path, *args, **kwargs):
        raise AssertionError(f"path os.chmod called on {path!r}")

    monkeypatch.setattr(blackbox_runner.os, "chmod", fail_chmod)
    guard = tmp_path / "guard"
    guard.mkdir()
    blackbox_runner.install_runner(guard, b'{"checks": [], "paths": [], "out": ""}')
    assert stat.S_IMODE((guard / f"{blackbox_runner.MODULE}.py").stat().st_mode) == 0o644
    assert stat.S_IMODE((guard / blackbox_runner.INPUT_NAME).stat().st_mode) == 0o644

    out_path = tmp_path / "out.json"
    (guard / blackbox_runner.INPUT_NAME).write_text(
        f'{{"checks": [], "paths": [], "out": "{out_path}"}}', encoding="utf-8"
    )
    ns: dict[str, object] = {"__file__": str(guard / f"{blackbox_runner.MODULE}.py")}
    exec(compile(blackbox_runner._RUNNER_SOURCE, "runner", "exec"), ns)
    main = ns["main"]
    assert callable(main)
    main()
    assert stat.S_IMODE(out_path.stat().st_mode) == 0o644


def test_copy_helper_uses_nofollow_fd_and_preserves_tree(tmp_path: Path) -> None:
    src = tmp_path / "src"
    (src / "sub").mkdir(parents=True)
    (src / "sub" / "f.txt").write_text("hello\n", encoding="utf-8")
    (src / "sub" / "f.txt").chmod(0o640)
    (src / "sub").chmod(0o750)
    (src / "lnk").symlink_to("sub/f.txt")
    dst = tmp_path / "dst"

    probe = (
        "import os\n"
        "real_chmod, real_utime = os.chmod, os.utime\n"
        "def bad_chmod(p, *a, **k): raise AssertionError(f'path chmod: {p!r}')\n"
        "def check_utime(p, *a, **k):\n"
        "    assert isinstance(p, int), f'path utime: {p!r}'\n"
        "    return real_utime(p, *a, **k)\n"
        "os.chmod, os.utime = bad_chmod, check_utime\n" + COPY_HELPER
    )
    done = subprocess.run(
        [sys.executable, "-I", "-S", "-c", probe, str(src), str(dst)],
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stderr
    assert stat.S_IMODE((dst / "sub").stat().st_mode) == 0o750
    assert stat.S_IMODE((dst / "sub" / "f.txt").stat().st_mode) == 0o640
    assert (dst / "lnk").is_symlink() and os.readlink(dst / "lnk") == "sub/f.txt"


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
