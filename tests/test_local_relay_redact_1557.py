"""#1557: text that reaches the trail, the ledger and the result goes through redact."""

import json
from dataclasses import asdict

import pytest

from experimental.local_relay import safe_summary
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


def _go(
    *, worker=_ok, acceptance=None, gate=None, ledger=None, plan_for=None, fallback=None, mask=False
):
    ledger = ledger or Ledger()
    result = run_relay(
        "t",
        worker,
        acceptance or (lambda _c: Acceptance(True, "fine")),
        gate or Gate(),
        fallback or (lambda _t: Attempt("cloud", "haiku", 1, 1, 0.0)),
        ledger,
        RelayConfig(enabled=True, plan_for=plan_for, mask_secret_lines=mask),
    )
    return result, ledger


def _everything(result, ledger) -> str:
    return json.dumps([asdict(result), ledger.rows], default=str)


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


DIRTY = lambda _c: Acceptance(False, f"FAILED token={MARK}")  # noqa: E731


class Verdict(Gate):
    def __init__(self, verdict="ACCEPT", reason=None):
        super().__init__()
        self.verdict, self.reason = verdict, reason

    def review(self, request):
        return GateReply(self.verdict, "gate-model", "medium", self.reason)


def _plan_incomplete(_c):
    raise PlanIncomplete("part of the change cannot be shown")


def _exit_cases(monkeypatch):
    secret_plan = f"GITHUB_TOKEN={MARK} trailing-sensitive-text"
    return {
        "accepted": dict(acceptance=lambda _c: Acceptance(True, f"ok token={MARK}")),
        "plan_incomplete": dict(acceptance=DIRTY, plan_for=_plan_incomplete),
        "gate_raised": dict(acceptance=DIRTY, gate=Gate(RuntimeError("boom"))),
        "rejected": dict(acceptance=DIRTY, gate=Verdict("REJECT")),
        "secret_blocked": dict(acceptance=DIRTY, plan_for=lambda _c: secret_plan, mask=True),
    }


@pytest.mark.parametrize(
    "case", ["accepted", "plan_incomplete", "gate_raised", "rejected", "secret_blocked"]
)
def test_acceptance_raw_is_redacted_on_every_exit(monkeypatch, case):
    if case == "secret_blocked":  # the summary must fail its own scan, or the gate is called
        monkeypatch.setattr(safe_summary, "_summary", lambda *_a, **_k: secret_line())
    result, ledger = _go(**_exit_cases(monkeypatch)[case])
    assert result.stages and MARK not in _everything(result, ledger)
    assert result.acceptance_raw is not None and "[REDACTED]" in result.acceptance_raw
    assert all("[REDACTED]" in e["acceptance"] for e in result.stages)


def secret_line():
    return f"GITHUB_TOKEN={MARK} trailing-sensitive-text"


def test_guard_plan_still_gets_the_original_acceptance_text():
    sent = []

    class Spy(Verdict):
        def review(self, request):
            sent.append(request.plan)
            return super().review(request)

    plan = f"+++ b/x.py\n+GITHUB_TOKEN={MARK}\n"
    raw = "x" * 2100 + " token: 7 passed"  # redaction eats the 7, so only the original has it
    _go(
        acceptance=lambda _c: Acceptance(False, raw),
        gate=Spy(),
        plan_for=lambda _c: plan,
        mask=True,
    )
    assert sent and "passed=7" in sent[0] and MARK not in sent[0]


def test_long_acceptance_json_stays_parseable():
    body = json.dumps({"status": "REFUSED", "unexpected_paths": [f"p/{i}.py" for i in range(300)]})
    assert len(body) > 2000  # past the old 2000-char redact cap
    result, _ = _go(acceptance=lambda _c: Acceptance(False, body))
    assert result.acceptance_raw is not None
    assert len(json.loads(result.acceptance_raw)["unexpected_paths"]) == 300


def test_gate_reply_reason_is_redacted():
    result, ledger = _go(gate=Verdict("REJECT", f"token={MARK}"))
    assert result.reason and MARK not in _everything(result, ledger)


def test_none_stays_none():
    from experimental.local_relay.relay import _safe

    assert _safe(None) is None and _safe("a") == "a"
