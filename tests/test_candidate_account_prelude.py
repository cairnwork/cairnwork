"""#803 item 2: a verifier-account refusal in the candidate stage. Only a prelude refusal
(``available`` false, ``require_idle`` or ``scrub_stale_runs`` refusing) before any candidate code
ran as the account is no evidence (UNCOMPARABLE, isolated worker only); ``AccountBusy`` and any
failure after the candidate ran stay ``INFRA_ERROR``. Fakes only: no real account is used."""
# ruff: noqa: F811 - the stub fixture is imported from test_uncomparable_paths

import subprocess
from contextlib import contextmanager
from typing import Any

import pytest
from test_pipeline import EditWorker
from test_uncomparable_paths import TEETH, IsolatedWorker, run_worker, stub  # noqa: F401
from verify_helpers import FIX

from cairnwork import pipeline_judge, verifiers
from cairnwork.account_prelude import AccountUnusableBeforeRun, locked_prelude
from cairnwork.journal import Journal, no_evidence
from cairnwork.verifier_account import AccountBusy, VerifierAccountError
from cairnwork.verify_context import VerifyContext

PASSING_P2P = {"rc": 0, "outcomes": {"tests/test_calc.py::test_add": "passed"}}
NOT_USABLE = "account cairnwork-verifier is not usable"


class FakeAccount:
    """The prelude's view of an account: refuses at ``step`` (or nowhere)."""

    user, python = "cairnwork-verifier", "python3"

    def __init__(self, step="", run_root=None):
        self.step, self.calls, self.run_root = step, [], run_root

    @contextmanager
    def exclusive(self, timeout=None):
        yield

    def available(self, python=None):
        self.calls.append("available")
        return self.step != "available"

    def require_idle(self):
        self.calls.append("require_idle")
        if self.step == "require_idle":
            raise VerifierAccountError("account cairnwork-verifier still runs processes (1)")

    def scrub_stale_runs(self):
        self.calls.append("scrub")
        if self.step == "scrub":
            raise VerifierAccountError("a stale run directory is left")


@pytest.mark.parametrize("step", ["available", "require_idle", "scrub"])
def test_each_prelude_refusal_is_typed_and_nothing_after_it_runs(step):
    account: Any = FakeAccount(step)
    with pytest.raises(AccountUnusableBeforeRun) as raised, locked_prelude(account, "python3", 1):
        pytest.fail("the run must not start")
    assert account.calls[-1] == {"scrub": "scrub"}.get(step, step)
    assert not raised.value.detail.startswith("VERIFIER_ACCOUNT_ERROR")


def test_a_usable_account_runs_and_its_errors_are_not_prelude_refusals():
    account: Any = FakeAccount()
    with pytest.raises(VerifierAccountError) as raised, locked_prelude(account, "python3", 1):
        raise VerifierAccountError("the run's own error")
    assert type(raised.value) is VerifierAccountError  # an error of the run stays what it was
    assert account.calls == ["available", "require_idle", "scrub"]


def test_run_verifiers_raises_the_prelude_refusal_before_any_checkout(project, tmp_path):
    account: Any = FakeAccount("require_idle", tmp_path / "account-runs")
    ctx = VerifyContext(project, tmp_path / "runs", account=account, python="python3")
    argv = ["python3", "-m", "pytest", "-q", "tests/test_calc.py"]
    spec = verifiers.parse_verifier("pytest", argv)
    head = subprocess.check_output(["git", "-C", str(project), "rev-parse", "HEAD"], text=True)
    with pytest.raises(AccountUnusableBeforeRun):
        verifiers.run_verifiers(ctx, head.strip(), (spec,), "r1")
    assert account.calls == ["available", "require_idle"]  # the run never started
    assert not (tmp_path / "account-runs").exists()


def _candidate_raises(error, times=1):
    """The candidate's own verifier run (``-v``) raises ``error`` the first ``times`` calls."""
    real, seen = pipeline_judge.run_verifiers, []

    def fake(ctx, commit, specs, run_id, **kw):
        if run_id.endswith("-v") and len(seen) < times:
            seen.append(run_id)
            raise error
        return real(ctx, commit, specs, run_id, **kw)

    return fake, seen


def _finished(tmp_path, result):
    return no_evidence(Journal(tmp_path / "wstate" / "journal.sqlite").entries(result.task_id))


@pytest.mark.parametrize("step", ["available", "require_idle", "scrub"])
def test_a_prelude_refusal_before_the_candidate_ran_is_uncomparable(
    project,
    tmp_path,
    stub,
    monkeypatch,
    step,
):
    calls = stub(TEETH, PASSING_P2P)
    error = AccountUnusableBeforeRun(f"{step}: {NOT_USABLE}")
    fake, _ = _candidate_raises(error)
    monkeypatch.setattr(pipeline_judge, "run_verifiers", fake)
    result = run_worker(project, tmp_path, IsolatedWorker({"src/calc.py": FIX}))
    assert result.status == "FAILED" and calls == []
    assert result.reason.startswith("UNCOMPARABLE: VERIFIER_ACCOUNT_UNUSABLE: "), result.reason
    assert _finished(tmp_path, result) == "VERIFIER_ACCOUNT_UNUSABLE"


def test_a_not_isolated_worker_keeps_the_infrastructure_error(
    project,
    tmp_path,
    stub,
    monkeypatch,
):
    stub(TEETH, PASSING_P2P)
    fake, _ = _candidate_raises(AccountUnusableBeforeRun(NOT_USABLE))
    monkeypatch.setattr(pipeline_judge, "run_verifiers", fake)
    result = run_worker(project, tmp_path, EditWorker({"src/calc.py": FIX}))
    assert result.reason.startswith("INFRA_ERROR: VerifierAccountError"), result.reason
    assert _finished(tmp_path, result) == ""


def test_a_prelude_refusal_after_the_candidate_ran_is_never_uncomparable(
    project,
    tmp_path,
    stub,
    monkeypatch,
):
    """The P2P runs come after the candidate's verifier run: the candidate ran as the account."""
    stub(TEETH, PASSING_P2P)
    monkeypatch.setattr(pipeline_judge, "run_plan", _raise(AccountUnusableBeforeRun(NOT_USABLE)))
    result = run_worker(project, tmp_path, IsolatedWorker({"src/calc.py": FIX}))
    assert "UNCOMPARABLE" not in result.reason, result.reason
    assert result.reason.startswith("INFRA_ERROR: VerifierAccountError")
    assert _finished(tmp_path, result) == ""


def _raise(error):
    def fail(*a, **k):
        raise error

    return fail


def test_a_busy_account_twice_is_never_uncomparable(
    project,
    tmp_path,
    stub,
    monkeypatch,
):
    stub(TEETH, PASSING_P2P)
    fake, seen = _candidate_raises(AccountBusy("RESOURCE_LEASE_TIMEOUT"), times=2)
    monkeypatch.setattr(pipeline_judge, "run_verifiers", fake)
    result = run_worker(project, tmp_path, IsolatedWorker({"src/calc.py": FIX}))
    assert len(seen) == 2 and "UNCOMPARABLE" not in result.reason, result.reason
    assert result.reason.startswith("INFRA_ERROR: AccountBusy")
    assert _finished(tmp_path, result) == ""


def test_a_busy_account_once_is_asked_again(project, tmp_path, stub, monkeypatch):
    stub(TEETH, PASSING_P2P)
    fake, seen = _candidate_raises(AccountBusy("RESOURCE_LEASE_TIMEOUT"), times=1)
    monkeypatch.setattr(pipeline_judge, "run_verifiers", fake)
    with pytest.raises(AssertionError, match="NOTARIZING"):  # run_worker: Core must not be asked
        run_worker(project, tmp_path, IsolatedWorker({"src/calc.py": FIX}))
    assert len(seen) == 1  # the second ask ran the candidate and Core judged it (NOTARIZING)
