import json
from dataclasses import dataclass
from typing import Any

import pytest

from experimental.local_relay.config import RelayConfig
from experimental.local_relay.ledger import JsonlLedger
from experimental.local_relay.relay import Acceptance, Attempt, LocalAttemptFailed, run_relay
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

    def __call__(self, *args: Any) -> Any:
        self.calls.append(args)
        value = next(self.values)
        if isinstance(value, Exception):
            raise value
        return value


@pytest.fixture(autouse=True)
def _flag_on(monkeypatch):
    monkeypatch.setenv("CAIRNWORK_LOCAL_RELAY", "1")


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
    assert worker.calls == [(32768,)] and len(acceptance.calls) == len(gate.calls) == 1
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
    assert result.route == "LOCAL_RETRY" and worker.calls == [(32768,), (65536,)]
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
    assert worker.calls == [(32768,), (65536,)] and len(gate.calls) == 2 and fallback.calls == []


def test_ac3_context_limit_counts_failed_local_call_then_retries_once():
    worker = Counter([EndpointContextLimit("too long"), attempt("good")])
    acceptance, gate, fallback, ledger = (
        Counter([Acceptance(True, "pass")]),
        FakeGate(["ACCEPT"]),
        Counter([attempt("cloud")]),
        FakeLedger(),
    )
    result = run_relay("t", worker, acceptance, gate, fallback, ledger, enabled())
    assert result.route == "LOCAL_RETRY" and worker.calls == [(32768,), (65536,)]
    assert len(acceptance.calls) == len(gate.calls) == 1 and fallback.calls == []
    assert [r["stage"] for r in ledger.rows] == ["local", "retry", "retry"]


def test_local_retry_and_fallback_failures_preserve_reasons_in_ledger_and_result():
    worker = Counter(
        [
            LocalAttemptFailed("wrapper refused: missing tmp"),
            LocalAttemptFailed("process_exit=0; episode_status=CONTEXT_LIMIT"),
        ]
    )
    ledger = FakeLedger()
    result = run_relay(
        "task",
        worker,
        lambda _: pytest.fail("incomplete episodes cannot reach acceptance"),
        FakeGate([]),
        lambda _task: (_ for _ in ()).throw(LocalAttemptFailed("proxy episode refused")),
        ledger,
        enabled(),
    )
    assert result.route == "FAILED"
    assert result.reason is not None
    assert "local: wrapper refused: missing tmp" in result.reason
    assert "retry: process_exit=0; episode_status=CONTEXT_LIMIT" in result.reason
    assert "fallback: proxy episode refused" in result.reason
    assert [row["failure"] for row in ledger.rows] == [
        "wrapper refused: missing tmp",
        "process_exit=0; episode_status=CONTEXT_LIMIT",
        "proxy episode refused",
    ]
    assert all(row["input_tokens"] == row["output_tokens"] == 0 for row in ledger.rows)


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
    assert result.route == "FALLBACK" and worker.calls == [(32768,), (65536,)]
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
    assert worker.calls == [(32768,), (65536,)] and len(fallback.calls) == 1
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
    assert worker.calls == [(32768,), (65536,)] and len(gate.calls) == 2 and fallback.calls == []


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
    assert worker.calls == [(32768,), (65536,)] and len(gate.calls) == 2 and fallback.calls == []
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
    base_keys = {"task_id", "stage", "model_id", "input_tokens", "output_tokens", "usd"}
    assert set(rows[0]) == base_keys  # local rows carry only the base fields
    gate_extra = {
        "acceptance_passed",
        "acceptance",
        "gate_verdict",
        "gate_reason",
        "gate_called",
        "gate_text",
    }
    assert set(rows[1]) == base_keys | gate_extra  # gate rows carry the per-stage evidence
    assert rows[1]["gate_verdict"] == "ACCEPT" and rows[1]["acceptance_passed"] is True
    assert rows[0] == {
        "task_id": "task-1",
        "stage": "local",
        "model_id": "local-9b",
        "input_tokens": 11,
        "output_tokens": 7,
        "usd": 0.0,
    }
    assert {k: rows[1][k] for k in base_keys} == {
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
    assert result.route == "LEDGER_ERROR" and (result.reason or "").startswith("disk full")
    assert worker.calls == [(32768,)] and acceptance.calls == []
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


@pytest.mark.parametrize("flag", [None, "true", "0", ""])
def test_config_enabled_true_cannot_bypass_the_environment_flag(monkeypatch, flag):
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
    result = run_relay("t", worker, acceptance, gate, fallback, ledger, enabled())
    assert result.route == "DISABLED" and result.reason == "RELAY_DISABLED"
    assert (worker.calls, acceptance.calls, gate.calls, fallback.calls, ledger.rows) == (
        [],
        [],
        [],
        [],
        [],
    )


def test_gate_accept_never_flips_an_acceptance_failure_at_any_stage():
    worker = Counter([attempt("one"), attempt("two")])
    acceptance = Counter(
        [Acceptance(False, "f1"), Acceptance(False, "f2"), Acceptance(False, "f3")]
    )
    gate, fallback, ledger = (
        FakeGate(["ACCEPT", "ACCEPT", "ACCEPT"]),
        Counter([attempt("cloud", "haiku", 0.1)]),
        FakeLedger(),
    )
    result = run_relay("t", worker, acceptance, gate, fallback, ledger, enabled())
    assert result.route == "FAILED" and result.acceptance_passed is False
    assert result.acceptance_raw == "f3" and len(gate.calls) == 3
    assert worker.calls == [(32768,), (65536,)] and len(fallback.calls) == 1


def test_every_stage_records_acceptance_and_gate_evidence_in_the_result():
    worker = Counter([attempt("one"), attempt("two")])
    acceptance = Counter(
        [Acceptance(False, "fail one"), Acceptance(False, "fail two"), Acceptance(True, "cloud ok")]
    )
    gate = FakeGate(["REJECT", "ACCEPT", "ACCEPT"])
    fallback = Counter([attempt("cloud", "haiku", 0.1)])
    result = run_relay("t", worker, acceptance, gate, fallback, FakeLedger(), enabled())
    assert result.route == "FALLBACK"
    assert [(s["stage"], s["acceptance_passed"], s["gate_verdict"]) for s in result.stages] == [
        ("local", False, "REJECT"),
        ("retry", False, "ACCEPT"),
        ("fallback", True, "ACCEPT"),
    ]
    assert result.stages[0]["gate_reason"] == "GATE_REJECTED"
    assert all(s["gate_called"] is True for s in result.stages)


def test_worker_failures_appear_in_the_stage_trail_without_a_gate_entry():
    worker = Counter([EndpointContextLimit("too long"), EndpointContextLimit("too long")])
    acceptance = Counter([Acceptance(True, "ok")])
    gate = FakeGate(["ACCEPT"])
    fallback = Counter([attempt("cloud", "haiku", 0.1)])
    result = run_relay("t", worker, acceptance, gate, fallback, FakeLedger(), enabled())
    kinds = [("worker_failure" in s, s["stage"]) for s in result.stages]
    assert kinds[:2] == [(True, "local"), (True, "retry")]
    assert result.stages[2]["stage"] == "fallback" and result.stages[2]["gate_verdict"] == "ACCEPT"
