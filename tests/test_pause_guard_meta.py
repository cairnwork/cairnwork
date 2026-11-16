"""PAUSE guard_meta: an ENOENT from the ACL read means "no ACL" only while the entry is still the
one stat-ed before (#566). Platform independent: it tests the helper the macOS branch calls."""

import os
import subprocess
import sys

import pytest

from experimental.pause.guard_meta import _no_acl_if_present


def test_enoent_with_entry_unchanged_means_no_acl(tmp_path):
    f = tmp_path / "f"
    f.write_text("x")
    st = os.stat(f, follow_symlinks=False)
    assert _no_acl_if_present(os.fsencode(f), st) == b""


def test_enoent_with_entry_gone_raises(tmp_path):
    f = tmp_path / "f"
    f.write_text("x")
    st = os.stat(f, follow_symlinks=False)
    f.unlink()
    with pytest.raises(OSError):
        _no_acl_if_present(os.fsencode(f), st)


def test_enoent_with_inode_changed_raises(tmp_path):
    f = tmp_path / "f"
    f.write_text("x")
    st = os.stat(f, follow_symlinks=False)
    f.unlink()
    f.write_text("y")
    other = os.stat(f, follow_symlinks=False)
    if other.st_ino == st.st_ino:  # inode reused: model the swap with a different recorded inode
        st = os.stat_result((*st[:1], st.st_ino + 1, *st[2:]))
    with pytest.raises(OSError):
        _no_acl_if_present(os.fsencode(f), st)


_DARWIN = pytest.mark.skipif(sys.platform != "darwin", reason="macOS ACL branch")


def _vanish_unlink(p):
    p.unlink()


def _vanish_replace_fifo(p):
    p.unlink()
    os.mkfifo(p)


def _vanish_replace_dir(p):
    p.unlink()
    p.mkdir()


@_DARWIN
@pytest.mark.parametrize("vanish", [_vanish_unlink, _vanish_replace_fifo, _vanish_replace_dir])
def test_darwin_acl_get_fails_closed_when_entry_vanished_or_replaced(tmp_path, vanish):
    from experimental.pause import guard_meta

    p = tmp_path / "e"
    os.mkfifo(p)
    st = os.lstat(p)
    vanish(p)
    with pytest.raises(OSError):
        guard_meta._acl_get(os.fsencode(p), st)


@_DARWIN
def test_darwin_acl_get_keeps_present_entries(tmp_path):
    from experimental.pause import guard_meta

    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    assert guard_meta._acl_get(os.fsencode(fifo), os.lstat(fifo)) == b""
    link = tmp_path / "dangling"
    os.symlink(tmp_path / "nowhere", link)
    assert guard_meta._acl_get(os.fsencode(link), os.lstat(link)) == b""
    reg = tmp_path / "reg"
    reg.write_text("x")
    subprocess.run(["chmod", "+a", "everyone deny write", str(reg)], check=True)
    out = guard_meta._acl_get(os.fsencode(reg), os.lstat(reg))
    assert out.startswith(b"!#acl 1")
