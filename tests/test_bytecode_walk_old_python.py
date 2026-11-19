"""bytecode_walk.py runs under the verifier's Python, which may be 3.6 (#807): no os.scandir(fd)
(3.7+), no str.isascii (3.7+). Listing through os.listdir(fd) + os.stat(dir_fd=, follow_symlinks=
False) keeps every entry resolved relative to its own directory descriptor, never through a link."""

import ast
import importlib.util
import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from cairnwork import bytecode_walk
from cairnwork.bytecode_roots import redirected_bytecode

MAGIC = importlib.util.MAGIC_NUMBER
PRELUDE = """
import os
_scandir = os.scandir
def scandir(path=None):
    if isinstance(path, int):
        raise TypeError("scandir: path should be string, bytes, os.PathLike or None, not int")
    return _scandir(path)
os.scandir = scandir
"""
OLD = sorted(Path.home().glob(".local/share/uv/python/cpython-3.[89]*/bin/python3"))


def tree(tmp_path: Path) -> tuple[Path, Path]:
    checkout = tmp_path / "r1-v0.work" / "r1-v0"
    run_dir = tmp_path / "r1-v0.abc" / "run"
    for directory in (checkout / "tests", checkout / ".git", run_dir / "home"):
        directory.mkdir(parents=True)
    (checkout / "tests" / "helper.py").write_text("OK = False\n")
    return checkout, run_dir


def plant(checkout: Path, run_dir: Path, magic: bytes = MAGIC) -> None:
    """Bytecode of tests/helper.py where a run's pycache_prefix would make Python read it."""
    where = run_dir / "home" / str(checkout / "tests").lstrip("/")
    where.mkdir(parents=True)
    (where / "helper.cpython-312.pyc").write_bytes(magic + b"\0" * 12)


def python_without_fd_scandir(tmp_path: Path) -> str:
    """An interpreter that refuses os.scandir(<fd>) like 3.6: sys.executable behind a wrapper."""
    wrapper = tmp_path / "py36like"
    wrapper.write_text(
        f"#!{sys.executable}\nimport os, sys\n"
        f"assert sys.argv[1:4] == ['-I', '-S', '-c']\n"
        f"os.execv({sys.executable!r}, [{sys.executable!r}, '-I', '-S', '-c', "
        f"{PRELUDE!r} + sys.argv[4], *sys.argv[5:]])\n"
    )
    wrapper.chmod(0o755)
    return str(wrapper)


def test_listing_does_not_need_scandir_on_a_descriptor(tmp_path, monkeypatch):
    top = tmp_path / "d"
    (top / "sub").mkdir(parents=True)
    (top / "link").symlink_to(top / "sub")
    (top / "x.cpython-312.pyc").write_bytes(MAGIC)
    (top / "data.bin").write_bytes(MAGIC + b"1")
    (top / "plain.txt").write_text("hi")

    def refuse(*_a: Any, **_k: Any):
        raise TypeError("scandir: path should be string, bytes, os.PathLike or None, not int")

    monkeypatch.setattr(os, "scandir", refuse)
    here, found = bytecode_walk.opened(str(top))
    kinds = {name: (kind, error) for name, kind, _t, error in found}
    assert here == (os.stat(top).st_dev, os.stat(top).st_ino)
    assert kinds == {
        "sub": ("dir", None),
        "link": ("link", None),  # not followed: its target is judged by resolve_links
        "x.cpython-312.pyc": ("other", None),
        "data.bin": ("bytecode", None),
        "plain.txt": ("other", None),
    }
    assert stat.S_ISDIR(os.stat(top / "link").st_mode)  # the link does point at a directory


def test_helper_uses_no_python_3_7_api():
    tree_ = ast.parse(Path(bytecode_walk.__file__).read_text())
    used = {n.attr for n in ast.walk(tree_) if isinstance(n, ast.Attribute)}
    assert not {"scandir", "isascii"} & used


def test_a_clean_run_has_no_finding_under_an_interpreter_without_fd_scandir(tmp_path):
    checkout, run_dir = tree(tmp_path)
    python = python_without_fd_scandir(tmp_path)
    assert redirected_bytecode(checkout, (checkout.parent, run_dir), python=python) == ()


def test_a_planted_pyc_is_still_found_without_fd_scandir(tmp_path):
    checkout, run_dir = tree(tmp_path)
    plant(checkout, run_dir)
    python = python_without_fd_scandir(tmp_path)
    found = redirected_bytecode(checkout, (checkout.parent, run_dir), python=python)
    assert found and not any("bytecode scan failed" in item for item in found)
    assert found == ("tests/helper.py (protected input)",)


@pytest.mark.skipif(not OLD, reason="no older uv Python (3.6 is not installable by uv)")
@pytest.mark.parametrize("planted", [False, True])
def test_helper_runs_under_a_real_older_python(tmp_path, planted):
    checkout, run_dir = tree(tmp_path)
    if planted:
        code = "import importlib.util,sys;sys.stdout.buffer.write(importlib.util.MAGIC_NUMBER)"
        magic = subprocess.run([str(OLD[-1]), "-c", code], capture_output=True, check=True).stdout
        plant(checkout, run_dir, magic)
    found = redirected_bytecode(checkout, (checkout.parent, run_dir), python=str(OLD[-1]))
    assert not any("bytecode scan failed" in item for item in found)
    assert bool(found) is planted


def closed_tree(tmp_path: Path) -> tuple[Path, Path, Path]:
    top, outside = tmp_path / "top", tmp_path / "outside"
    (top / "mid" / "closed").mkdir(parents=True)
    (outside / "closed").mkdir(parents=True)
    (top / "mid" / "closed" / "f").write_text("x")
    (top / "mid" / "closed").chmod(0)
    (outside / "closed").chmod(0)
    return top, top / "mid", outside


def test_a_closed_directory_is_opened_through_handles(tmp_path):
    top, _, _ = closed_tree(tmp_path)
    here, found = bytecode_walk.opened(str(top / "mid" / "closed"))
    assert here == bytecode_walk.ident(os.stat(top / "mid" / "closed"))
    assert [name for name, *_ in found] == ["f"]
    assert stat.S_IMODE((top / "mid" / "closed").stat().st_mode) == 0o700


def test_a_middle_component_swapped_for_a_symlink_changes_no_mode_outside(tmp_path, monkeypatch):
    top, mid, outside = closed_tree(tmp_path)
    real_lstat, swapped = os.lstat, []

    def lstat_then_swap(path, *args, **kwargs):
        info = real_lstat(path, *args, **kwargs)
        if not swapped:  # the run's live process replaces ``mid`` after the check
            swapped.append(1)
            mid.rename(top / "mid.real")
            mid.symlink_to(outside)
        return info

    monkeypatch.setattr(bytecode_walk.os, "lstat", lstat_then_swap)
    with pytest.raises(OSError):
        bytecode_walk.opened(str(mid / "closed"))
    assert stat.S_IMODE((outside / "closed").stat().st_mode) == 0


LINUX = hasattr(os, "O_PATH")  # chmod through the checked handle; macOS chmods the name


def swap_closed(mid: Path, put, monkeypatch, at: str) -> tuple[list[int], list[str]]:
    """``mid/closed`` is moved aside and ``put(mid / "closed")`` takes its place: ``at`` "check"
    just before the identity check (the first stat or open of the name by handle), "chmod" just
    before the chmod (after the check). Returns (swapped, the paths chmodded)."""
    real_stat, real_open, real_chmod = os.stat, os.open, os.chmod
    swapped: list[int] = []
    chmods: list[str] = []

    def swap() -> None:
        if not swapped:
            swapped.append(1)
            (mid / "closed").rename(mid / "closed.real")
            put(mid / "closed")

    def by_handle(real):
        def call(path, *args, **kwargs):
            if at == "check" and path == "closed" and kwargs.get("dir_fd") is not None:
                swap()
            return real(path, *args, **kwargs)

        return call

    def chmod_recorded(path, mode, *args, **kwargs):
        chmods.append(str(path))
        if at == "chmod":
            swap()
        return real_chmod(path, mode, *args, **kwargs)

    monkeypatch.setattr(os, "stat", by_handle(real_stat))
    monkeypatch.setattr(os, "open", by_handle(real_open))
    monkeypatch.setattr(os, "chmod", chmod_recorded)
    return swapped, chmods


def test_a_last_component_swapped_for_a_symlink_is_refused_before_any_chmod(tmp_path, monkeypatch):
    top, mid, outside = closed_tree(tmp_path)
    swapped, chmods = swap_closed(
        mid, lambda at: at.symlink_to(outside / "closed"), monkeypatch, "check"
    )
    with pytest.raises(OSError):
        bytecode_walk.opened(str(mid / "closed"))
    assert swapped and (mid / "closed").is_symlink()  # the swap really happened
    assert chmods == []  # nothing was chmodded, not even the link
    assert stat.S_IMODE((outside / "closed").stat().st_mode) == 0


def test_a_symlink_swapped_in_after_the_check_changes_no_mode_outside(tmp_path, monkeypatch):
    top, mid, outside = closed_tree(tmp_path)
    swapped, chmods = swap_closed(
        mid, lambda at: at.symlink_to(outside / "closed"), monkeypatch, "chmod"
    )
    with pytest.raises(OSError):  # the swapped-in link is not a directory the listing may use
        bytecode_walk.opened(str(mid / "closed"))
    assert swapped and (mid / "closed").is_symlink()
    assert stat.S_IMODE((outside / "closed").stat().st_mode) == 0
    # The chmod went through the checked handle (Linux) or by name without following (macOS).
    assert len(chmods) == 1 and (
        chmods[0].startswith("/proc/self/fd/") if LINUX else chmods[0] == "closed"
    )


def test_a_real_directory_swapped_in_after_the_check_is_refused(tmp_path, monkeypatch):
    """Codex review of #1327: a different real directory renamed in between the identity check and
    the chmod. Linux chmods the checked inode, so the newcomer keeps its mode; macOS cannot open a
    closed directory, chmods by name, and refuses the listing when the name is no longer it."""
    top, mid, _ = closed_tree(tmp_path)
    (mid / "newcomer").mkdir(mode=0)
    swapped, chmods = swap_closed(mid, (mid / "newcomer").rename, monkeypatch, "chmod")
    with pytest.raises(OSError):
        bytecode_walk.opened(str(mid / "closed"))
    assert swapped and not (mid / "newcomer").exists() and (mid / "closed").is_dir()
    assert len(chmods) == 1
    if LINUX:
        assert stat.S_IMODE((mid / "closed").stat().st_mode) == 0  # the newcomer is untouched
        assert stat.S_IMODE((mid / "closed.real").stat().st_mode) == 0o700  # the checked one
    (mid / "closed").chmod(0o700)  # let tmp_path cleanup in


@pytest.mark.parametrize("ancestor", [0o311, 0o111, 0o755])
@pytest.mark.parametrize("closed", [0o700, 0o000, 0o500])
def test_any_mode_that_a_path_lookup_can_use_works_as_on_main(tmp_path, ancestor, closed):
    """main chmods and opens by path, which needs only x on the ancestors; so must this. Checked
    on main: every combination lists ``f`` and leaves the directory at 0o700."""
    top, mid, _ = closed_tree(tmp_path)
    (mid / "closed").chmod(closed)
    for directory in (mid, top):
        directory.chmod(ancestor)
    try:
        here, found = bytecode_walk.opened(str(mid / "closed"))
    finally:
        top.chmod(0o755)
        mid.chmod(0o755)
    assert [name for name, *_ in found] == ["f"]
    assert here == bytecode_walk.ident(os.stat(mid / "closed"))
    assert stat.S_IMODE((mid / "closed").stat().st_mode) == 0o700
