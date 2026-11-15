"""PAUSE guard_meta: an ENOENT from the ACL read means "no ACL" only while the entry is still the
one stat-ed before (#566). Platform independent: it tests the helper the macOS branch calls."""

import os

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
