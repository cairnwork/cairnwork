"""socket_paths marks a searchable-but-unlistable directory as blind (#418): connect() needs only
search (x), so a socket under it is reachable yet never listed or probed. A directory that cannot
be searched either is only unreadable: nothing under it can be reached by path."""

import os

import pytest
from test_run_isolation_witness import socket_paths


@pytest.mark.skipif(os.geteuid() == 0, reason="root lists and searches every directory")
def test_searchable_unlistable_dir_is_blind_closed_dir_is_not(tmp_path):
    root = os.path.realpath(tmp_path)
    blind_dir, closed_dir = os.path.join(root, "blind"), os.path.join(root, "closed")
    os.mkdir(blind_dir)
    os.mkdir(closed_dir)
    os.chmod(blind_dir, 0o311)  # search, no list
    os.chmod(closed_dir, 0o000)
    try:
        _, unreadable, blind, _ = socket_paths((root,), 100, 4)
    finally:
        os.chmod(blind_dir, 0o700)
        os.chmod(closed_dir, 0o700)
    assert unreadable == [blind_dir, closed_dir]
    assert blind == [blind_dir]
