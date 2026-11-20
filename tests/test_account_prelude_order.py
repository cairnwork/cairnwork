"""#568: the prelude probes (``available``, ``require_idle``, ``scrub_stale_runs``) run only while
the account lock is held, in the real ``run_verifiers`` call and in ``locked_prelude``. A probe
outside the lock would let a timed-out probe's ``kill_all()`` (#561) end another session's run.
The source check (test_available_under_lock) reads text; this one drives the call. Fakes only."""

import subprocess
from contextlib import contextmanager
from typing import Any

import pytest

from cairnwork import verifiers
from cairnwork.account_prelude import AccountUnusableBeforeRun, locked_prelude
from cairnwork.verifier_account import VerifierAccountError
from cairnwork.verify_context import VerifyContext


class LockOrderAccount:
    """Records ``lock``/``unlock`` and each probe; ``refuse`` makes ``require_idle`` fail."""

    user, python = "cairnwork-verifier", "python3"

    def __init__(self, run_root=None, refuse=False):
        self.events, self.run_root, self.refuse = [], run_root, refuse

    @contextmanager
    def exclusive(self, timeout=None):
        self.events.append("lock")
        try:
            yield
        finally:
            self.events.append("unlock")

    def available(self, python=None):
        self.events.append("available")
        return True

    def require_idle(self):
        self.events.append("require_idle")
        if self.refuse:
            raise VerifierAccountError("account still runs processes (1)")

    def scrub_stale_runs(self):
        self.events.append("scrub")


def test_every_probe_sits_between_lock_and_unlock():
    account: Any = LockOrderAccount()
    with locked_prelude(account, "python3", 1):
        account.events.append("run")
    assert account.events == ["lock", "available", "require_idle", "scrub", "run", "unlock"]


def test_a_refusal_still_releases_the_lock_after_the_probes():
    account: Any = LockOrderAccount(refuse=True)
    with pytest.raises(AccountUnusableBeforeRun), locked_prelude(account, "python3", 1):
        pytest.fail("the run must not start")
    assert account.events == ["lock", "available", "require_idle", "unlock"]


def test_run_verifiers_probes_the_account_only_under_the_lock(project, tmp_path):
    account: Any = LockOrderAccount(tmp_path / "account-runs", refuse=True)
    ctx = VerifyContext(project, tmp_path / "runs", account=account, python="python3")
    spec = verifiers.parse_verifier(
        "pytest", ["python3", "-m", "pytest", "-q", "tests/test_calc.py"]
    )
    head = subprocess.check_output(["git", "-C", str(project), "rev-parse", "HEAD"], text=True)
    with pytest.raises(AccountUnusableBeforeRun):
        verifiers.run_verifiers(ctx, head.strip(), (spec,), "r1")
    assert account.events == ["lock", "available", "require_idle", "unlock"]
