"""#437 H: a timed-out account cleanup ends its own child (SIGTERM first, sudo relays it) and is
reported as a failure. Synthetic: ``subprocess.Popen`` is a stand-in, so no sudo and no account.
"""

import subprocess

import pytest

import cairnwork.verifier_account as va
from cairnwork.verifier_account import VerifierAccount, VerifierAccountError


class _Child:
    pids = 4242

    def __init__(self, hangs, ignores_term=False):
        self.hangs, self.ignores_term, self.events = hangs, ignores_term, []

    def wait(self, timeout=None):
        self.events.append(("wait", timeout))
        stuck = self.hangs and not (
            "terminate" in [e[0] for e in self.events] and not self.ignores_term
        )
        if "kill" in [e[0] for e in self.events]:
            stuck = False
        if stuck and timeout is not None:
            raise subprocess.TimeoutExpired("sudo", timeout)
        return 0

    def terminate(self):
        self.events.append(("terminate",))

    def kill(self):
        self.events.append(("kill",))


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
