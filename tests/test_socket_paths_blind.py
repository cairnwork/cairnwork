"""socket_paths marks a searchable-but-unlistable directory as blind (#418): connect() needs only
search (x), so a socket under it is reachable yet never listed or probed. A directory that cannot
be searched either is only unreadable: nothing under it can be reached by path. The owner side
fails on a blind directory unless a root listing matches its evidenced inventory, and the in-run
probe fails on one on Linux."""

import inspect
import os
import types

import pytest
from socket_inventory import owner_sockets
from test_run_isolation_witness import PROBE, connected_types, socket_paths

ALLOWED = {"/r/ok": ("/r/ok/known.sock",)}


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
    _, failures = owner_sockets([], blind, ALLOWED, lambda path: [])
    assert failures == ["BLIND " + repr(blind_dir)]  # not allowlisted: fails, sudo not asked


def test_allowlisted_dir_passes_only_on_its_evidenced_inventory():
    def lists(found):
        return lambda path: found

    host, failures = owner_sockets(["/h.sock"], ["/r/ok"], ALLOWED, lists(["/r/ok/known.sock"]))
    assert (host, failures) == (["/h.sock", "/r/ok/known.sock"], [])  # probed by name
    extra = ["/r/ok/known.sock", "/r/ok/s/shim.sock"]
    _, failures = owner_sockets([], ["/r/ok"], ALLOWED, lists(extra))
    assert failures == ["BLIND-INVENTORY " + repr("/r/ok/s/shim.sock")]
    _, failures = owner_sockets([], ["/r/ok"], ALLOWED, lists(None))  # no root listing
    assert failures == ["BLIND " + repr("/r/ok")]


def test_in_run_probe_fails_on_a_blind_dir_on_linux(tmp_path, monkeypatch):
    walker = "def socket_paths(roots, limit, depth):\n    return [], ['/b'], ['/b'], 1\n"
    code = PROBE.format(
        walker=walker, connector=inspect.getsource(connected_types), host=[], roots=(),
        limit=1, depth=1,
    )  # fmt: skip
    namespace = {}
    exec(code, namespace)  # noqa: S102
    namespace["sys"] = types.SimpleNamespace(platform="linux")
    namespace["own_socket_connects"] = lambda: ["SOCK_STREAM"]
    monkeypatch.setenv("HOME", str(tmp_path / "a" / "b" / "c"))
    with pytest.raises(AssertionError, match="searchable dirs not listed"):
        namespace["test_no_host_socket_is_connectable"]()
