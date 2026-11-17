import os
import stat

import pytest

from experimental.pause.guard import TreeGuard
from experimental.pause.interfaces import GuardUnavailable


def _pair(checkout):
    return TreeGuard(checkout).digest_pair(exclude_git=False, exclude_paths=frozenset())


def test_a_symlink_out_of_the_checkout_stops_the_guard_before_it_starts(tmp_path):
    checkout = tmp_path / "checkout"
    outside = tmp_path / "outside"
    checkout.mkdir()
    outside.mkdir()
    (outside / "secret").write_text("x")
    (checkout / "link").symlink_to(outside / "secret")
    with pytest.raises(GuardUnavailable, match="points outside the checkout"):
        TreeGuard(checkout).digest()


def test_a_symlink_inside_the_checkout_still_starts(tmp_path):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "file").write_text("x")
    (checkout / "link").symlink_to("file")
    assert TreeGuard(checkout).digest()


def test_a_flag_change_found_when_the_plan_arrives_stops_the_run(tmp_path):
    if not hasattr(os, "chflags") or not hasattr(stat, "UF_HIDDEN"):
        pytest.skip("file flags are unavailable")
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    path = checkout / "file"
    path.write_text("x")
    before = TreeGuard(checkout).digest()  # unbound: chflags also moves ctime, which would mask it
    try:
        os.chflags(path, stat.UF_HIDDEN)
        assert TreeGuard(checkout).digest() != before
    finally:
        os.chflags(path, 0)


def test_an_mtime_only_change_found_when_the_plan_arrives_stops_the_run(tmp_path):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    path = checkout / "file"
    path.write_text("x")
    st = path.stat()
    before = TreeGuard(checkout, bind_times=True).digest()
    unbound = TreeGuard(checkout).digest()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns - 1_000_000_000))
    assert TreeGuard(checkout, bind_times=True).digest() != before
    assert TreeGuard(checkout).digest() == unbound


def test_the_core_tree_comparison_ignores_times(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    (a / "file").write_text("same")
    (b / "file").write_text("same")
    os.utime(b / "file", ns=(1_000_000_000, 1_000_000_000))
    assert TreeGuard(a).digest() == TreeGuard(b).digest()
    assert _pair(a) == _pair(b)


def test_a_special_file_stops_the_guard_before_it_starts(tmp_path):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    os.mkfifo(checkout / "pipe")
    with pytest.raises(GuardUnavailable, match="is not a file, directory or symlink"):
        TreeGuard(checkout).digest()


def test_a_special_file_in_the_pair_walk_also_stops(tmp_path):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    os.mkfifo(checkout / "pipe")
    with pytest.raises(GuardUnavailable, match="is not a file, directory or symlink"):
        _pair(checkout)
