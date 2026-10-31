"""#537 under a verifier account: an interrupt neither waits without limit on a process the owner
cannot signal, nor leaves the account's checkout behind (no recovery step removes ``.work`` on
the cairnwork-verify path). Stand-ins only: no account is used and no process is started or
signalled here."""

import io
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import cairnwork.blackbox_exec as blackbox_exec
import cairnwork.confined as confined
import cairnwork.verifiers as verifiers

STOPS = [KeyboardInterrupt(), SystemExit(3)]


class StubProcess:  # Popen whose timed wait is interrupted and that never exits by itself
    pid = 424242
    returncode = None

    def __init__(self, stop, waits):
        self.stdout = io.BytesIO(b"")
        self.stop, self.waits = stop, waits

    def wait(self, timeout=None):
        self.waits.append(timeout)
        if len(self.waits) == 1:
            raise self.stop
        if timeout is None:  # a sudo whose child the owner cannot kill: this never returns
            raise AssertionError("waited without a limit after the interrupt")
        raise subprocess.TimeoutExpired("sudo", timeout)


def _execute(monkeypatch, tmp_path, stop, reap):
    waits, killed = [], []
    monkeypatch.setattr(confined.subprocess, "Popen", lambda *a, **k: StubProcess(stop, waits))
    monkeypatch.setattr(confined, "_kill_group", killed.append)
    env = {confined.RUN_TOKEN_VAR: "t"}
    with pytest.raises(type(stop)):
        confined.execute(["x"], tmp_path, env, 5, tmp_path / "log", reap=reap)
    return waits, killed


@pytest.mark.parametrize("stop", STOPS, ids=type)
def test_an_interrupt_reaps_the_account_and_waits_a_bounded_time(monkeypatch, tmp_path, stop):
    reaped = []
    waits, killed = _execute(monkeypatch, tmp_path, stop, lambda: reaped.append(1) or 0)
    assert killed == [StubProcess.pid]
    assert reaped == [1]
    assert waits[0] == 5 and waits[1] is not None


def test_a_failing_reap_does_not_replace_the_interrupt(monkeypatch, tmp_path):
    def reap():
        raise RuntimeError("probe failed")

    waits, killed = _execute(monkeypatch, tmp_path, KeyboardInterrupt(), reap)
    assert killed == [StubProcess.pid] and waits[1] is not None


class StubAccount:  # account.remove is the only removal of the account's checkout
    def __init__(self, run_root: Path):
        self.run_root, self.removed = run_root, []

    def remove(self, path: Path) -> None:
        self.removed.append(path)


def _account_checkout(account, python, repo, commit, work, clone_id):
    checkout = work / clone_id
    checkout.mkdir()
    return checkout


def _raise(exc):
    def raiser(*args, **kwargs):
        raise exc

    return raiser


def _context(tmp_path) -> Any:
    account = StubAccount(tmp_path / "account-runs")
    return SimpleNamespace(repo=tmp_path, run_root=account.run_root, attempt_id="", account=account)


@pytest.mark.parametrize("stop", STOPS, ids=type)
def test_an_interrupt_still_removes_the_account_verifier_checkout(monkeypatch, tmp_path, stop):
    ctx = _context(tmp_path)
    monkeypatch.setattr(verifiers, "make_account_checkout", _account_checkout)
    monkeypatch.setattr(verifiers, "_run_one", _raise(stop))
    batch: Any = SimpleNamespace(ctx=ctx, python="/usr/bin/python3", candidate_commit="c" * 40)
    spec = SimpleNamespace(verifier_id="v")
    with pytest.raises(type(stop)):
        verifiers._run_all(batch, (spec,), "a1-v")
    work = ctx.run_root / "a1-v-v0.work"
    assert ctx.account.removed == [work]
    assert not work.exists()


@pytest.mark.parametrize("stop", STOPS, ids=type)
def test_an_interrupt_still_removes_the_account_blackbox_checkout(monkeypatch, tmp_path, stop):
    ctx = _context(tmp_path)
    monkeypatch.setattr(blackbox_exec, "make_account_checkout", _account_checkout)
    monkeypatch.setattr(blackbox_exec, "_run_in", _raise(stop))
    with pytest.raises(type(stop)):
        blackbox_exec._run(object(), "c" * 40, "a1-v", 5, (), ctx, "/usr/bin/python3")
    work = ctx.run_root / "a1-v-vbb.work"
    assert ctx.account.removed == [work]
    assert not work.exists()
