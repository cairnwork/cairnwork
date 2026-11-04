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
