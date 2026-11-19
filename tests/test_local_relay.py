import json
from dataclasses import dataclass

import pytest

from experimental.local_relay.config import RelayConfig
from experimental.local_relay.ledger import JsonlLedger
from experimental.local_relay.relay import Acceptance, Attempt, run_relay
from experimental.local_worker.endpoint import EndpointContextLimit
from experimental.pause.interfaces import GateReply


class FakeLedger:
    def __init__(self, fail_at=None):
        self.rows = []
        self.fail_at = fail_at

    def append(self, row):
        if self.fail_at == len(self.rows):
            raise OSError("disk full")
        self.rows.append(row)


class FakeGate:
    def __init__(self, verdicts, usd=0.02):
        self.verdicts = iter(verdicts)
        self.calls = []
        self.usd = usd

    def review(self, request):
        self.calls.append(request)
        verdict = next(self.verdicts)
        return (
            MeteredGateReply(
                verdict=verdict,
                model_id="gate-model",
                effort="medium",
                input_tokens=13,
                output_tokens=5,
                usd=self.usd,
            )
            if isinstance(verdict, str)
            else verdict
        )


@dataclass(frozen=True)
class MeteredGateReply(GateReply):
    input_tokens: int = 13
    output_tokens: int = 5
    usd: float = 0.02


class Counter:
    def __init__(self, values):
        self.values = iter(values)
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)
        value = next(self.values)
        if isinstance(value, Exception):
            raise value
        return value


def enabled(**kwargs):
    return RelayConfig(enabled=True, **kwargs)


def attempt(name, model="local-9b", usd=0.0):
    return Attempt(name, model, 11, 7, usd)


@pytest.mark.parametrize("flag", [None, "true", "0", ""])
def test_ac1_disabled_values_never_call_dependencies(monkeypatch, flag):
    if flag is None:
        monkeypatch.delenv("CAIRNWORK_LOCAL_RELAY", raising=False)
    else:
        monkeypatch.setenv("CAIRNWORK_LOCAL_RELAY", flag)
    worker, acceptance, fallback = (
        Counter([attempt("x")]),
        Counter([Acceptance(True, "ok")]),
        Counter([attempt("f")]),
    )
    gate, ledger = FakeGate(["ACCEPT"]), FakeLedger()
    result = run_relay("t", worker, acceptance, gate, fallback, ledger)
    assert result.route == "DISABLED" and result.reason == "RELAY_DISABLED"
    assert (worker.calls, acceptance.calls, gate.calls, fallback.calls, ledger.rows) == (
        [],
        [],
        [],
        [],
        [],
    )


def test_ac2_local_acceptance_and_gate_accept_routes_local_first_and_accounts_gate():
    worker, acceptance, fallback = (
        Counter([attempt("patch")]),
        Counter([Acceptance(True, "raw pass")]),
        Counter([attempt("cloud")]),
    )
    ledger, gate = FakeLedger(), FakeGate(["ACCEPT"])
    result = run_relay("t", worker, acceptance, gate, fallback, ledger, enabled())
    assert result.route == "LOCAL_FIRST" and result.acceptance_raw == "raw pass"
    assert worker.calls == [(8192,)] and len(acceptance.calls) == len(gate.calls) == 1
    assert fallback.calls == []
    assert [(r["stage"], r["usd"]) for r in ledger.rows] == [("local", 0.0), ("local", 0.02)]


def test_ac2_acceptance_pass_with_gate_reject_enters_retry():
    worker = Counter([attempt("first"), attempt("second")])
    acceptance = Counter([Acceptance(True, "pass one"), Acceptance(True, "pass two")])
    gate, fallback, ledger = (
        FakeGate(["REJECT", "ACCEPT"]),
        Counter([attempt("cloud")]),
        FakeLedger(),
    )
    result = run_relay("t", worker, acceptance, gate, fallback, ledger, enabled())
    assert result.route == "LOCAL_RETRY" and worker.calls == [(8192,), (65536,)]
    assert len(gate.calls) == len(acceptance.calls) == 2 and fallback.calls == []
    assert [r["stage"] for r in ledger.rows] == ["local", "local", "retry", "retry"]


def test_ac3_acceptance_failure_then_retry_success_is_exactly_two_attempts():
    worker = Counter([attempt("bad"), attempt("good")])
    acceptance = Counter([Acceptance(False, "original fail"), Acceptance(True, "original pass")])
    gate, fallback, ledger = (
        FakeGate(["ACCEPT", "ACCEPT"]),
        Counter([attempt("cloud")]),
        FakeLedger(),
    )
    result = run_relay("t", worker, acceptance, gate, fallback, ledger, enabled())
    assert result.route == "LOCAL_RETRY" and result.acceptance_raw == "original pass"
    assert worker.calls == [(8192,), (65536,)] and len(gate.calls) == 2 and fallback.calls == []


def test_ac3_context_limit_counts_failed_local_call_then_retries_once():
    worker = Counter([EndpointContextLimit("too long"), attempt("good")])
    acceptance, gate, fallback, ledger = (
        Counter([Acceptance(True, "pass")]),
        FakeGate(["ACCEPT"]),
        Counter([attempt("cloud")]),
        FakeLedger(),
    )
    result = run_relay("t", worker, acceptance, gate, fallback, ledger, enabled())
    assert result.route == "LOCAL_RETRY" and worker.calls == [(8192,), (65536,)]
    assert len(acceptance.calls) == len(gate.calls) == 1 and fallback.calls == []
    assert [r["stage"] for r in ledger.rows] == ["local", "retry", "retry"]


def test_ac3_second_local_failure_falls_back_without_third_worker_call():
    worker = Counter([attempt("one"), attempt("two")])
    acceptance = Counter(
        [
            Acceptance(False, "fail one"),
            Acceptance(False, "fail two"),
            Acceptance(True, "cloud pass"),
        ]
    )
    gate, fallback, ledger = (
        FakeGate(["ACCEPT", "ACCEPT", "ACCEPT"]),
        Counter([attempt("cloud", "haiku", 0.12)]),
        FakeLedger(),
    )
    result = run_relay("t", worker, acceptance, gate, fallback, ledger, enabled())
    assert result.route == "FALLBACK" and worker.calls == [(8192,), (65536,)]
    assert len(acceptance.calls) == len(gate.calls) == 3 and len(fallback.calls) == 1
    assert [r["stage"] for r in ledger.rows].count("fallback") == 2


def test_ac4_rejected_fallback_is_failed_and_is_not_retried():
    worker = Counter([attempt("one"), attempt("two")])
    acceptance = Counter(
        [Acceptance(False, "no"), Acceptance(False, "no"), Acceptance(True, "yes")]
    )
    gate, fallback, ledger = (
        FakeGate(["REJECT", "ACCEPT", "REJECT"]),
        Counter([attempt("cloud")]),
        FakeLedger(),
    )
    result = run_relay("t", worker, acceptance, gate, fallback, ledger, enabled())
    assert result.route == "FAILED" and result.acceptance_passed is True
    assert worker.calls == [(8192,), (65536,)] and len(fallback.calls) == 1
    assert len(gate.calls) == len(acceptance.calls) == 3


def test_ac5_acceptance_failure_with_gate_accept_still_retries():
    worker = Counter([attempt("bad"), attempt("good")])
    acceptance = Counter([Acceptance(False, "verbatim failure"), Acceptance(True, "verbatim pass")])
    gate, fallback, ledger = (
        FakeGate(["ACCEPT", "ACCEPT"]),
        Counter([attempt("cloud")]),
        FakeLedger(),
    )
    result = run_relay("t", worker, acceptance, gate, fallback, ledger, enabled())
    assert result.route == "LOCAL_RETRY" and result.acceptance_raw == "verbatim pass"
    assert worker.calls == [(8192,), (65536,)] and len(gate.calls) == 2 and fallback.calls == []


def test_ac5_unknown_gate_verdict_is_failure_with_reason_code():
    worker = Counter([attempt("one"), attempt("two")])
    acceptance = Counter([Acceptance(True, "pass"), Acceptance(True, "pass")])
    gate, fallback, ledger = (
        FakeGate(["MAYBE", "ACCEPT"]),
        Counter([attempt("cloud")]),
        FakeLedger(),
    )
    result = run_relay("t", worker, acceptance, gate, fallback, ledger, enabled())
    assert result.route == "LOCAL_RETRY" and result.gate_verdict == "ACCEPT"
    assert worker.calls == [(8192,), (65536,)] and len(gate.calls) == 2 and fallback.calls == []
    assert len(ledger.rows) == 4


def test_ac6_jsonl_ledger_appends_complete_cloud_and_zero_cost_local_rows(tmp_path):
    path = tmp_path / "cost.jsonl"
    result = run_relay(
        "task-1",
        Counter([attempt("patch")]),
        Counter([Acceptance(True, "ok")]),
        FakeGate(["ACCEPT"]),
        Counter([attempt("cloud")]),
        JsonlLedger(path),
        enabled(),
    )
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert result.route == "LOCAL_FIRST" and len(rows) == 2
    assert all(
        set(row) == {"task_id", "stage", "model_id", "input_tokens", "output_tokens", "usd"}
        for row in rows
    )
    assert rows[0] == {
        "task_id": "task-1",
        "stage": "local",
        "model_id": "local-9b",
        "input_tokens": 11,
        "output_tokens": 7,
        "usd": 0.0,
    }
    assert rows[1] == {
        "task_id": "task-1",
        "stage": "local",
        "model_id": "gate-model",
        "input_tokens": 13,
        "output_tokens": 5,
        "usd": 0.02,
    }


def test_ac6_ledger_failure_stops_before_any_gate_or_fallback_call():
    worker, acceptance, fallback = (
        Counter([attempt("patch")]),
        Counter([Acceptance(True, "ok")]),
        Counter([attempt("cloud")]),
    )
    gate, ledger = FakeGate(["ACCEPT"]), FakeLedger(fail_at=0)
    result = run_relay("t", worker, acceptance, gate, fallback, ledger, enabled())
    assert result.route == "LEDGER_ERROR" and result.reason.startswith("disk full")
    assert worker.calls == [(8192,)] and acceptance.calls == []
    assert gate.calls == fallback.calls == ledger.rows == []


def test_gate_exception_fails_closed_without_retry_or_fallback():
    class BrokenGate:
        def __init__(self):
            self.calls = 0

        def review(self, request):
            self.calls += 1
            raise RuntimeError("gate offline")

    worker, acceptance, fallback, gate, ledger = (
        Counter([attempt("patch")]),
        Counter([Acceptance(True, "ok")]),
        Counter([attempt("cloud")]),
        BrokenGate(),
        FakeLedger(),
    )
    result = run_relay("t", worker, acceptance, gate, fallback, ledger, enabled())
    assert result.route == "FAILED" and result.reason == "gate offline"
    assert len(worker.calls) == len(acceptance.calls) == gate.calls == 1
    assert fallback.calls == []
