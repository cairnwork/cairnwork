"""#1828: the u+r opening of a closed regular file gives the same result on every platform.

On Linux ``os.chmod`` cannot be told not to follow a link, so the opening goes through an
O_PATH handle checked to be the listed inode. In process: bytecode_walk.opened.
"""

import os
import stat
from typing import Any

import pytest

from cairnwork import bytecode_walk

pytestmark = pytest.mark.skipif(os.geteuid() == 0, reason="chmod 0 does not close a file to root")
MAGIC = bytecode_walk.MAGIC


def _entries(path) -> dict[str, tuple[Any, ...]]:
    found = bytecode_walk.opened(str(path))[1]
    return {name: (kind, error) for name, kind, _ident, error in found}


@pytest.mark.parametrize("where", ["flat", "in_a_closed_directory"])
def test_an_honest_closed_file_gives_no_finding(tmp_path, where):
    top = tmp_path / "top"
    top.mkdir()
    closed = top / "closed.txt"
    closed.write_text("x = 1\n")
    closed.chmod(0)
    if where == "in_a_closed_directory":
        top.chmod(0)
    assert _entries(top)["closed.txt"] == ("other", None)
    assert stat.S_IMODE(closed.stat().st_mode) & 0o400


def test_a_closed_file_starting_with_the_magic_number_is_bytecode(tmp_path):
    closed = tmp_path / "hidden"
    closed.write_bytes(MAGIC + b"rest")
    closed.chmod(0)
    assert _entries(tmp_path)["hidden"] == ("bytecode", None)


def test_a_closed_file_whose_chmod_is_refused_is_an_error(tmp_path, monkeypatch):
    closed = tmp_path / "closed"
    closed.write_text("x\n")
    closed.chmod(0)

    def no_chmod(*args, **kwargs):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(os, "chmod", no_chmod)
    assert _entries(tmp_path)["closed"][1] is not None


def test_a_symlink_entry_is_never_chmodded(tmp_path):
    victim = tmp_path / "victim"
    victim.write_text("x\n")
    victim.chmod(0o200)
    (tmp_path / "link").symlink_to(victim)
    _entries(tmp_path)
    assert stat.S_IMODE(victim.stat().st_mode) == 0o200


def test_a_closed_hardlinked_file_is_an_error_and_keeps_its_mode(tmp_path):
    closed = tmp_path / "closed"
    closed.write_text("x\n")
    other = tmp_path.parent / (tmp_path.name + "_other")
    os.link(closed, other)
    closed.chmod(0)
    try:
        assert _entries(tmp_path)["closed"][1] is not None
        assert stat.S_IMODE(closed.stat().st_mode) == 0
    finally:
        other.unlink()
