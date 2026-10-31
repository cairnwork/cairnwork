"""A timed-out available() probe leaves nothing running as the account (Issue #560).

All stubs: no sudo is run and the real account is never touched.
"""

import subprocess
from types import SimpleNamespace
from typing import Any

import pytest

from cairnwork import pytest_config, verifier_account
from cairnwork.verifier_account import VerifierAccount


class _Backend:
    def policy(self, **_kw: Any) -> Any:
        return SimpleNamespace(text="(version 1)")


@pytest.fixture
def account(tmp_path, monkeypatch):
    acct = VerifierAccount(user="probe-stub", root=tmp_path)
    monkeypatch.setattr(verifier_account.pwd, "getpwnam", lambda _u: SimpleNamespace(pw_uid=54321))
    monkeypatch.setattr(verifier_account, "_backend", lambda: _Backend())
    monkeypatch.setattr(pytest_config, "interpreter_roots", lambda _py: ())
    monkeypatch.setattr(VerifierAccount, "wrap", lambda self, argv, profile, env=None: ["stub"])
    return acct


def _run_returning(monkeypatch, outcome: Any):
    def fake(*_a, **_kw):
        if isinstance(outcome, BaseException):
            raise outcome
        return SimpleNamespace(returncode=outcome)

    monkeypatch.setattr(verifier_account.subprocess, "run", fake)


def _record_kill_all(monkeypatch, result: Any = 0) -> list[int]:
    calls: list[int] = []

    def fake(self, attempts: int = 5):
        calls.append(attempts)
        if isinstance(result, BaseException):
            raise result
        return result

    monkeypatch.setattr(VerifierAccount, "kill_all", fake)
    return calls


def test_a_timed_out_probe_clears_the_account(account, monkeypatch):
    _run_returning(monkeypatch, subprocess.TimeoutExpired(["stub"], 60))
    calls = _record_kill_all(monkeypatch)
    assert account.available() is False
    assert calls == [5]


def test_a_probe_that_finishes_does_not_clear_the_account(account, monkeypatch):
    _run_returning(monkeypatch, 0)
    calls = _record_kill_all(monkeypatch)
    assert account.available() is True
    assert calls == []


def test_a_failed_probe_does_not_clear_the_account(account, monkeypatch):
    _run_returning(monkeypatch, 1)
    calls = _record_kill_all(monkeypatch)
    assert account.available() is False
    assert calls == []


@pytest.mark.parametrize("error", [OSError("sudo gone"), RuntimeError("boom")])
def test_a_failing_cleanup_still_reports_unavailable(account, monkeypatch, error):
    _run_returning(monkeypatch, subprocess.TimeoutExpired(["stub"], 60))
    calls = _record_kill_all(monkeypatch, error)
    assert account.available() is False
    assert calls == [5]


def test_the_policy_file_is_removed_after_a_timeout(account, monkeypatch, tmp_path):
    _run_returning(monkeypatch, subprocess.TimeoutExpired(["stub"], 60))
    _record_kill_all(monkeypatch)
    account.available()
    assert list(tmp_path.glob("*.policy")) == []


def test_a_probe_that_cannot_start_does_not_clear_the_account(account, monkeypatch):
    _run_returning(monkeypatch, OSError("no sudo"))
    calls = _record_kill_all(monkeypatch)
    assert account.available() is False
    assert calls == []
