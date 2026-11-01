"""socket_paths marks a searchable-but-unlistable directory as blind (#418): connect() needs only
search (x), so a socket under it is reachable yet never listed or probed. A directory that cannot
be searched either is only unreadable: nothing under it can be reached by path. The owner side
fails on a blind directory unless a clean root listing (sudo/find mocked here) matches its evidenced
inventory, socket_run stops before the run on such a failure, and the in-run probe fails on one on
Linux."""

import inspect
import os
import subprocess
import types

import pytest
import test_run_isolation_witness as witness
from socket_inventory import owner_sockets, sudo_sockets
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
    _, failures = owner_sockets([], blind, ALLOWED, lambda path: ([], []))
    assert failures == ["BLIND " + repr(blind_dir)]  # not allowlisted: fails, sudo not asked


def test_allowlisted_dir_passes_only_on_its_evidenced_inventory():
    def lists(found, links=()):
        return lambda path: (found, list(links))

    host, failures = owner_sockets(["/h.sock"], ["/r/ok"], ALLOWED, lists(["/r/ok/known.sock"]))
    assert (host, failures) == (["/h.sock", "/r/ok/known.sock"], [])  # probed by name
    extra = ["/r/ok/s/known.sock", "/r/ok/s/shim.sock"]  # full paths compared, at any depth
    _, failures = owner_sockets([], ["/r/ok"], ALLOWED, lists(["/r/ok/known.sock", *extra]))
    assert failures == ["BLIND-INVENTORY " + repr(p) for p in extra]
    # connect() follows a link and its target is not resolved: any link fails, even at a known path
    links = ["/r/ok/known.sock", "/r/ok/l"]
    host, failures = owner_sockets([], ["/r/ok"], ALLOWED, lists([], links))
    assert (host, failures) == ([], ["BLIND-INVENTORY " + repr(p) for p in links])
    _, failures = owner_sockets([], ["/r/ok"], ALLOWED, lambda path: None)  # no root listing
    assert failures == ["BLIND " + repr("/r/ok")]


def fake_find(monkeypatch, results):
    """Replace subprocess.run with one answering each find by its -type; return the argv list."""
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        result = results[argv[argv.index("-type") + 1]]
        if isinstance(result, BaseException):
            raise result
        code, out, err = result
        return subprocess.CompletedProcess(argv, code, "\0".join(out).encode(), err)

    monkeypatch.setattr(subprocess, "run", run)
    return calls


def test_root_listing_crosses_mounts_lists_links_and_counts_only_clean_output(monkeypatch):
    clean = (0, ["/r/ok/known.sock", "/r/ok/m/new.sock"], b"")
    calls = fake_find(monkeypatch, {"s": clean, "l": (0, ["/r/ok/l"], b"")})
    assert sudo_sockets("/r/ok") == (["/r/ok/known.sock", "/r/ok/m/new.sock"], ["/r/ok/l"])
    assert calls == [  # no -xdev: sockets below a nested mount are listed too
        ["sudo", "-n", "find", "/r/ok", "-type", kind, "-print0"] for kind in "sl"
    ]
    _, failures = owner_sockets([], ["/r/ok"], ALLOWED, sudo_sockets)
    assert failures == ["BLIND-INVENTORY " + repr(p) for p in ("/r/ok/l", "/r/ok/m/new.sock")]
    unclean = (
        (1, [], b"find: '/r/ok/x': Permission denied"),  # non-zero exit
        (0, ["/r/ok/known.sock"], b"find: '/r/ok/x': Permission denied"),  # exit 0, stderr
        (1, [], b""),
        OSError("no sudo"),
        subprocess.TimeoutExpired("sudo", 60),
    )
    for bad in unclean:
        for kind in "sl":
            fake_find(monkeypatch, {"s": clean, "l": (0, [], b""), kind: bad})
            assert sudo_sockets("/r/ok") is None, (kind, bad)


def test_socket_run_fails_on_blind_dirs_and_probes_evidenced_names(monkeypatch):
    def walk(roots, limit, depth):
        return ["/h.sock"], ["/r/ok"], ["/r/ok"], 1

    def owner(found):
        return lambda host, blind: owner_sockets(host, blind, ALLOWED, lambda path: (found, []))

    def no_run(*args):
        pytest.fail("the run started despite a blind dir")

    monkeypatch.setattr(witness, "socket_paths", walk)
    monkeypatch.setattr(witness, "run_one", no_run)
    monkeypatch.setattr(witness, "owner_sockets", owner(["/r/ok/known.sock", "/r/ok/new.sock"]))
    with pytest.raises(AssertionError, match="blind dirs or unlisted sockets"):
        witness.socket_run(None, None)
    probes = []
    monkeypatch.setattr(witness, "run_one", lambda repo, tmp, files, *rest: probes.append(files))
    monkeypatch.setattr(witness, "owner_sockets", owner(["/r/ok/known.sock"]))
    witness.socket_run(None, None)
    (files,) = probes
    assert "HOST = ['/h.sock', '/r/ok/known.sock']\n" in files["tests/test_sockets.py"]


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
