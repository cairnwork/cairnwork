"""#482 as policy text (macOS cannot run bwrap): the daemon-socket mask honours account reach, and
the private tree refuses an unbounded number of rebuilt links. Not run on Linux: no real mount."""

# ruff: noqa: F811, F401
import os

import pytest

from cairnwork_sandbox import bwrap, bwrap_private
from cairnwork_sandbox.backend import SandboxError
from tests.test_bwrap_private_trees import _at, _policy, _real, host


@pytest.fixture
def sock(host, monkeypatch):
    rd = host / "home" / ".rd"
    rd.mkdir()
    (rd / "docker.sock").write_text("")
    (host / "run" / "docker.sock").symlink_to(rd / "docker.sock")
    monkeypatch.setattr(bwrap, "_DANGEROUS_SOCKETS", (str(host / "run" / "docker.sock"),))
    return _real(rd / "docker.sock")


def _reach(monkeypatch, answer):
    seen = []
    monkeypatch.setattr(bwrap, "account_may_traverse", lambda d: seen.append(d) or answer)
    return seen


def test_reachable_socket_is_masked_in_account_mode(host, sock, monkeypatch):
    seen = _reach(monkeypatch, True)
    assert _at(_policy(host, owner=False), "--ro-bind", "/dev/null", sock) >= 0
    assert os.path.dirname(sock) in seen


def test_unreachable_socket_is_not_masked_in_account_mode(host, sock, monkeypatch):
    _reach(monkeypatch, False)
    assert _at(_policy(host, owner=False), "--ro-bind", "/dev/null", sock) == -1


@pytest.mark.parametrize("answer", [True, False])
def test_owner_mode_always_masks(host, sock, monkeypatch, answer):
    _reach(monkeypatch, answer)
    assert _at(_policy(host, owner=True), "--ro-bind", "/dev/null", sock) >= 0


def _links(host, count):
    (host / "tmp" / "t").write_text("")
    for i in range(count):
        (host / "tmp" / f"l{i}").symlink_to(host / "tmp" / "t")


def test_links_within_the_cap_are_rebuilt(host, monkeypatch):
    monkeypatch.setattr(bwrap_private, "MAX_TMP_LINKS", 5)
    _links(host, 5)
    lines = _policy(host)
    assert sum(1 for line in lines if line == "--symlink") == 5


def test_links_over_the_cap_are_refused(host, monkeypatch):
    monkeypatch.setattr(bwrap_private, "MAX_TMP_LINKS", 5)
    _links(host, 6)
    with pytest.raises(SandboxError, match="more than 5 links"):
        _policy(host)
