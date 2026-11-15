"""#437 H: a timed-out account cleanup ends its own child (SIGTERM first, sudo relays it) and is
reported as a failure. Synthetic: ``subprocess.Popen`` is a stand-in, so no sudo and no account.
"""

import subprocess

import pytest

import cairnwork.verifier_account as va
from cairnwork.verifier_account import VerifierAccount, VerifierAccountError


class _Child:
    def __init__(
        self,
        hangs,
        ignores_term=False,
        term_error=None,
        wait_error=None,
        kill_error=None,
        never_exits=False,
    ):
        self.hangs, self.ignores_term, self.events = hangs, ignores_term, []
        self.term_error, self.wait_error = term_error, wait_error
        self.kill_error, self.never_exits = kill_error, never_exits

    def wait(self, timeout=None):
        self.events.append(("wait", timeout))
        if self.wait_error and timeout == 60:
            raise self.wait_error
        stuck = self.hangs and not (
            "terminate" in [e[0] for e in self.events] and not self.ignores_term
        )
        if "kill" in [e[0] for e in self.events] and not self.never_exits:
            stuck = False
        if self.never_exits and timeout is not None and self.hangs:
            raise subprocess.TimeoutExpired("sudo", timeout)
        if stuck and timeout is not None:
            raise subprocess.TimeoutExpired("sudo", timeout)
        return 0

    def terminate(self):
        self.events.append(("terminate",))
        if self.term_error:
            raise self.term_error

    def kill(self):
        self.events.append(("kill",))
        if self.kill_error:
            raise self.kill_error


def _account(tmp_path):
    (tmp_path / "runs" / "private" / "run").mkdir(parents=True)
    return VerifierAccount(user="cairnwork-verifier", root=tmp_path), tmp_path / "runs/private/run"


def _patch(monkeypatch, child, spawned):
    def popen(argv, **kwargs):
        spawned.append(argv)
        return child

    monkeypatch.setattr(va.subprocess, "Popen", popen)
    monkeypatch.setattr(
        va.subprocess, "run", lambda *a, **k: pytest.fail("subprocess.run kills only sudo")
    )


def test_timeout_terminates_own_child_and_fails(tmp_path, monkeypatch):
    account, run = _account(tmp_path)
    child, spawned = _Child(hangs=True), []
    _patch(monkeypatch, child, spawned)
    with pytest.raises(VerifierAccountError, match="timed out"):
        account.remove(run)
    names = [e[0] for e in child.events]
    assert "terminate" in names and "kill" not in names
    assert len(spawned) == 1, "later argv must not run after a timeout"


def test_child_that_ignores_term_is_killed_after_the_grace(tmp_path, monkeypatch):
    account, run = _account(tmp_path)
    child = _Child(hangs=True, ignores_term=True)
    _patch(monkeypatch, child, [])
    with pytest.raises(VerifierAccountError, match="timed out"):
        account.remove(run)
    names = [e[0] for e in child.events]
    assert names.index("terminate") < names.index("kill")


def test_cleanup_that_finishes_is_untouched(tmp_path, monkeypatch):
    account, run = _account(tmp_path)
    child, spawned = _Child(hangs=False), []
    _patch(monkeypatch, child, spawned)
    account.remove(run)
    assert spawned and all(e[0] == "wait" for e in child.events)


def test_waits_are_the_documented_bounds(tmp_path, monkeypatch):
    account, run = _account(tmp_path)
    child = _Child(hangs=True, ignores_term=True)
    _patch(monkeypatch, child, [])
    with pytest.raises(VerifierAccountError):
        account.remove(run)
    assert child.events[0] == ("wait", 60) and ("wait", 5) in child.events


def test_terminate_that_cannot_signal_still_ends_with_a_kill_and_a_failure(tmp_path, monkeypatch):
    account, run = _account(tmp_path)
    child = _Child(hangs=True, ignores_term=True, term_error=PermissionError("denied"))
    _patch(monkeypatch, child, [])
    with pytest.raises(VerifierAccountError, match="timed out"):
        account.remove(run)
    assert "kill" in [e[0] for e in child.events]


def test_interrupt_kills_the_own_child_and_propagates(tmp_path, monkeypatch):
    account, run = _account(tmp_path)
    child = _Child(hangs=False, wait_error=KeyboardInterrupt())
    _patch(monkeypatch, child, [])
    with pytest.raises(KeyboardInterrupt):
        account.remove(run)
    assert "kill" in [e[0] for e in child.events]


def test_unsignalable_child_never_hangs_and_still_fails(tmp_path, monkeypatch):
    account, run = _account(tmp_path)
    err = PermissionError("denied")
    child = _Child(hangs=True, ignores_term=True, term_error=err, kill_error=err, never_exits=True)
    _patch(monkeypatch, child, [])
    with pytest.raises(VerifierAccountError, match="timed out"):
        account.remove(run)
    assert all(e[1] is not None for e in child.events if e[0] == "wait"), "no unbounded wait"


def test_interrupt_with_unkillable_child_still_propagates(tmp_path, monkeypatch):
    account, run = _account(tmp_path)
    child = _Child(
        hangs=True,
        wait_error=KeyboardInterrupt(),
        kill_error=PermissionError("x"),
        never_exits=True,
    )
    _patch(monkeypatch, child, [])
    with pytest.raises(KeyboardInterrupt):
        account.remove(run)
    assert all(e[1] is not None for e in child.events if e[0] == "wait")
