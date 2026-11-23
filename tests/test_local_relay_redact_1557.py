"""#1557: text that reaches the trail, the ledger and the result goes through redact."""

import json
from dataclasses import asdict

import pytest

from experimental.local_relay.config import RelayConfig
from experimental.local_relay.relay import (
    Acceptance,
    Attempt,
    LocalAttemptFailed,
    PlanIncomplete,
    run_relay,
)
from experimental.pause.interfaces import GateReply

MARK = "ghp_UNIQUEMARKER1557abcdef"


@pytest.fixture(autouse=True)
def _flag_on(monkeypatch):
    monkeypatch.setenv("CAIRNWORK_LOCAL_RELAY", "1")


class Ledger:
    def __init__(self, fail=False):
        self.rows, self.fail = [], fail

    def append(self, row):
        if self.fail:
            raise OSError(f"disk token={MARK}")
        self.rows.append(row)


class Gate:
    def __init__(self, error=None):
        self.error = error

    def review(self, request):
        if self.error:
            raise self.error
        return GateReply("ACCEPT", "gate-model", "medium")


def _ok(*_a):
    return Attempt("cand", "local-9b", 1, 1, 0.0)


def _go(*, worker=_ok, acceptance=None, gate=None, ledger=None, plan_for=None, fallback=None):
    ledger = ledger or Ledger()
    result = run_relay(
        "t",
        worker,
        acceptance or (lambda _c: Acceptance(True, "fine")),
        gate or Gate(),
        fallback or (lambda _t: Attempt("cloud", "haiku", 1, 1, 0.0)),
        ledger,
        RelayConfig(enabled=True, plan_for=plan_for),
    )
    return result, ledger


def _everything(result, ledger) -> str:
    return json.dumps([asdict(result), ledger.rows], default=str)


def _fields(result):
    return [result.reason, result.acceptance_raw, *[e.get("acceptance") for e in result.stages]]


def test_acceptance_output_is_redacted_in_trail_ledger_and_result():
    result, ledger = _go(acceptance=lambda _c: Acceptance(False, f"FAILED token={MARK}"))
    assert result.stages and MARK not in _everything(result, ledger)
    assert "[REDACTED]" in result.stages[0]["acceptance"]


def test_plan_failure_is_redacted():
    def boom(_c):
        raise RuntimeError(f"plan bad password={MARK}")

    result, ledger = _go(plan_for=boom, acceptance=lambda _c: Acceptance(True, f"api_key={MARK}"))
    assert result.route == "FAILED" and "plan failed" in result.stages[-1]["gate_reason"]
    assert MARK not in _everything(result, ledger)


def test_plan_incomplete_is_redacted():
    def boom(_c):
        raise PlanIncomplete(f"cut off Bearer {MARK}")

    result, ledger = _go(plan_for=boom)
    assert MARK not in _everything(result, ledger)


def test_gate_exception_is_redacted():
    result, ledger = _go(gate=Gate(RuntimeError(f"upstream secret: {MARK}")))
    assert result.route == "FAILED" and "gate raised" in result.stages[-1]["gate_reason"]
    assert MARK not in _everything(result, ledger)


def test_worker_failure_and_fallback_failure_are_redacted():
    def worker(*_a):
        raise LocalAttemptFailed(f"worker died token={MARK}")

    def fallback(_t):
        raise LocalAttemptFailed(f"proxy died Authorization: {MARK}")

    result, ledger = _go(worker=worker, fallback=fallback)
    assert result.route == "FAILED" and ledger.rows
    assert MARK not in _everything(result, ledger)


def test_ledger_error_reason_is_redacted():
    result, ledger = _go(ledger=Ledger(fail=True))
    assert result.route == "LEDGER_ERROR" and MARK not in _everything(result, ledger)


def test_clean_text_is_unchanged():
    result, _ = _go(acceptance=lambda _c: Acceptance(True, "5 passed in 0.1s"))
    assert result.acceptance_raw == "5 passed in 0.1s"
