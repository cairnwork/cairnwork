"""Offline acceptance tests for Issue #1513's local relay wiring."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from experimental.local_relay.adapters import (
    AgyFallback,
    AgyQuotaLedger,
    CoreAcceptance,
    CostGate,
    LocalWorkerAdapter,
)
from experimental.local_relay.cli import main
from experimental.local_relay.config import (
    DEFAULT_CONTEXT_TOKENS,
    DEFAULT_LEDGER_PATH,
    DEFAULT_LOCAL_ENDPOINT,
    RelayConfig,
)
from experimental.local_relay.ledger import JsonlLedger
from experimental.local_relay.relay import Acceptance, Attempt, LocalAttemptFailed, run_relay
from experimental.local_worker.endpoint import EndpointUnreachable
from experimental.pause.client import TransportResult
from experimental.pause.core_judge import CoreJudge
from experimental.pause.flow import RunResult


class FakeTransport:
    def __init__(self, verdict="ACCEPT", input_tokens=1000, output_tokens=500):
        self.verdict = verdict
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.calls = []

    def send(self, model, effort, messages, timeout):
        self.calls.append((model, effort, messages, timeout))
        return TransportResult(
            200,
            {
                "text": self.verdict,
                "usage": {
                    "input_tokens": self.input_tokens,
                    "output_tokens": self.output_tokens,
                },
            },
        )


class Rows:
    def __init__(self):
        self.rows = []

    def append(self, row):
        self.rows.append(row)


class AcceptingTransport:
    def __init__(self):
        self.calls = []

    def send(self, model, effort, messages, timeout):
        self.calls.append((model, effort, messages, timeout))
        return TransportResult(200, {"text": "ACCEPT", "usage": {}})


def _fixture_run(tmp_path: Path) -> RunResult:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "test@example.invalid"], check=True
    )
    (repo / "candidate.txt").write_text("before\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "candidate.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "base"], check=True)
    base = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    (repo / "candidate.txt").write_text("after\n", encoding="utf-8")
    return RunResult(0, "done", tmp_path, repo, base_sha=base, record={})


def test_w1_flag_off_refuses_before_runtime_or_endpoint(monkeypatch, capsys, tmp_path):
    monkeypatch.delenv("CAIRNWORK_LOCAL_RELAY", raising=False)
    calls = []
    code = main(
        ["--state", str(tmp_path), "--repo", "owner/repo", "--clone", str(tmp_path), "run-once"],
        run_once=lambda *_: calls.append("called") or {},
    )
    assert code == 2
    assert json.loads(capsys.readouterr().out)["error"] == "RELAY_DISABLED"
    assert calls == []


def test_w1_flag_on_uses_default_runtime_assembly(monkeypatch, tmp_path):
    monkeypatch.setenv("CAIRNWORK_LOCAL_RELAY", "1")
    calls = []
    monkeypatch.setattr(
        "experimental.local_relay.cli.build_run_once",
        lambda _args: lambda *_: calls.append(1) or {"ok": True},
    )
    args = ["--state", str(tmp_path), "--repo", "o/r", "--clone", str(tmp_path), "run-once"]
    assert main(args) == 0 and calls == [1]


def test_cli_run_once_forwards_issue_runner_arguments_and_state_path(monkeypatch, tmp_path):
    monkeypatch.setenv("CAIRNWORK_LOCAL_RELAY", "1")
    captured = []
    args = [
        "--state",
        str(tmp_path / "state"),
        "--repo",
        "owner/repo",
        "--clone",
        str(tmp_path),
        "--ledger",
        str(tmp_path / "ledger.jsonl"),
        "run-once",
    ]

    def runtime(parsed, ledger):
        captured.append((parsed.state, parsed.repo, parsed.clone))
        ledger.append({"task_id": "fixture", "stage": "local", "usd": 0})
        return {"ok": True}

    assert main(args, run_once=runtime) == 0
    assert captured == [(str(tmp_path / "state"), "owner/repo", str(tmp_path))]
    assert json.loads((tmp_path / "ledger.jsonl").read_text())["task_id"] == "fixture"


def test_w2_local_episode_runs_through_real_core_judge_in_fixture_repo(monkeypatch, tmp_path):
    monkeypatch.setenv("CAIRNWORK_LOCAL_RELAY", "1")
    result = _fixture_run(tmp_path)
    judge = CoreJudge(
        tmp_path / "state",
        (),
        verify=lambda *_args, **_kwargs: {"status": "VERIFIED"},
    )
    local = LocalWorkerAdapter(lambda _cap, _endpoint: result, token_usage=lambda _result: (7, 3))
    gate = CostGate(FakeTransport(), input_usd_per_million=1, output_usd_per_million=2)
    rows = Rows()
    outcome = run_relay(
        "fixture-task",
        local,
        CoreAcceptance(judge),
        gate,
        lambda _task: pytest.fail("fallback should not run"),
        rows,
        RelayConfig(enabled=True),
    )
    assert outcome.route == "LOCAL_FIRST" and outcome.acceptance_passed
    assert outcome.acceptance_raw is not None
    assert json.loads(outcome.acceptance_raw)["status"] == "VERIFIED"


def test_w2_core_nonverified_result_cannot_become_success(monkeypatch, tmp_path):
    monkeypatch.setenv("CAIRNWORK_LOCAL_RELAY", "1")
    result = _fixture_run(tmp_path)
    judge = CoreJudge(tmp_path / "state", (), verify=lambda *_a, **_k: {"status": "FAILED"})
    rows = Rows()
    outcome = run_relay(
        "task",
        LocalWorkerAdapter(lambda _cap, _endpoint: result),
        CoreAcceptance(judge),
        CostGate(FakeTransport()),
        lambda _task: Attempt(result, "agy"),
        rows,
        RelayConfig(enabled=True),
    )
    assert outcome.route == "FAILED" and outcome.acceptance_passed is False


def test_w3_context_defaults_are_first_32768_then_retry_65536():
    config = RelayConfig()
    assert config.default_context_tokens == DEFAULT_CONTEXT_TOKENS == 32768
    assert config.retry_context_tokens == 65536


def test_w3_worker_receives_exact_context_and_loopback_endpoint(tmp_path):
    result = _fixture_run(tmp_path)
    calls = []
    attempt = LocalWorkerAdapter(lambda cap, endpoint: calls.append((cap, endpoint)) or result)(
        32768
    )
    assert calls == [(32768, DEFAULT_LOCAL_ENDPOINT)]
    assert attempt.candidate is result


def test_w3_worker_rejects_incomplete_episode():
    local = LocalWorkerAdapter(lambda _cap, _endpoint: RunResult(1, "failed", Path("."), Path(".")))
    with pytest.raises(LocalAttemptFailed, match="did not produce"):
        local(32768)


def test_incomplete_local_episodes_retry_then_fallback_with_both_checks(monkeypatch):
    monkeypatch.setenv("CAIRNWORK_LOCAL_RELAY", "1")
    calls = []
    local = LocalWorkerAdapter(
        lambda cap, _endpoint: calls.append(cap) or RunResult(1, "failed", Path("."), Path("."))
    )
    checks = []
    transport = AcceptingTransport()
    gate = CostGate(transport)
    rows = Rows()
    outcome = run_relay(
        "task",
        local,
        lambda candidate: checks.append(("acceptance", candidate)) or Acceptance(True, "VERIFIED"),
        gate,
        lambda _task: Attempt("agy-candidate", "agy"),
        rows,
        RelayConfig(enabled=True),
    )
    assert outcome.route == "FALLBACK"
    assert calls == [32768, 65536]
    assert len(checks) == 1 and checks[0][0] == "acceptance"
    local_rows = [row for row in rows.rows if row["stage"] in {"local", "retry"}]
    assert [row["usd"] for row in local_rows] == [0.0, 0.0]
    assert len(transport.calls) == 1


def test_local_worker_programming_error_is_not_treated_as_attempt_failure(monkeypatch):
    monkeypatch.setenv("CAIRNWORK_LOCAL_RELAY", "1")
    local = LocalWorkerAdapter(lambda _cap, _endpoint: (_ for _ in ()).throw(TypeError("bug")))
    with pytest.raises(TypeError, match="bug"):
        run_relay(
            "task",
            local,
            lambda _: Acceptance(True, "ok"),
            CostGate(AcceptingTransport()),
            lambda _task: pytest.fail("programming error must not fall back"),
            Rows(),
            RelayConfig(enabled=True),
        )


def test_w4_fallback_is_existing_agy_worker_and_route_is_fallback(monkeypatch):
    monkeypatch.setenv("CAIRNWORK_LOCAL_RELAY", "1")
    worker = object()
    observed = []
    fallback = AgyFallback(
        worker,
        lambda found, task: observed.append((found, task)) or "agy-result",
        quota_units=lambda _result: 23,
    )
    gate = CostGate(FakeTransport())
    rows = Rows()

    def unavailable(_cap):
        raise EndpointUnreachable("offline")

    outcome = run_relay(
        "task",
        unavailable,
        lambda _candidate: Acceptance(True, "VERIFIED"),
        gate,
        fallback,
        AgyQuotaLedger(rows, fallback),
        RelayConfig(enabled=True),
    )
    assert outcome.route == "FALLBACK" and observed == [(worker, "task")]
    agy_row = next(row for row in rows.rows if row["model_id"] == "agy")
    assert agy_row["usd"] == 0 and agy_row["quota_units"] == 23


def test_w4_agy_fallback_exception_is_not_a_success(monkeypatch):
    monkeypatch.setenv("CAIRNWORK_LOCAL_RELAY", "1")
    fallback = AgyFallback(object(), lambda *_: (_ for _ in ()).throw(RuntimeError("agy failed")))

    def unavailable(_cap):
        raise EndpointUnreachable("offline")

    outcome = run_relay(
        "task",
        unavailable,
        lambda _: Acceptance(True, "ok"),
        CostGate(FakeTransport()),
        fallback,
        Rows(),
        RelayConfig(enabled=True),
    )
    assert outcome.route == "FAILED"


def test_w5_gate_usage_cost_is_recorded(monkeypatch):
    monkeypatch.setenv("CAIRNWORK_LOCAL_RELAY", "1")
    gate = CostGate(
        FakeTransport(input_tokens=1200, output_tokens=300),
        input_usd_per_million=1,
        output_usd_per_million=2,
    )
    rows = Rows()
    outcome = run_relay(
        "task",
        lambda _cap: Attempt("patch", "local-9b"),
        lambda _: Acceptance(True, "ok"),
        gate,
        lambda _: pytest.fail("unexpected fallback"),
        rows,
        RelayConfig(enabled=True),
    )
    assert outcome.route == "LOCAL_FIRST"
    assert rows.rows[-1]["usd"] == pytest.approx(0.0018)


def test_w5_missing_usage_records_zero_cost_without_inventing_tokens(monkeypatch):
    monkeypatch.setenv("CAIRNWORK_LOCAL_RELAY", "1")
    rows = Rows()
    outcome = run_relay(
        "task",
        lambda _cap: Attempt("patch", "local-9b"),
        lambda _: Acceptance(True, "ok"),
        CostGate(FakeTransport(input_tokens=0, output_tokens=0)),
        lambda _: pytest.fail("unexpected fallback"),
        rows,
        RelayConfig(enabled=True),
    )
    assert outcome.route == "LOCAL_FIRST"
    assert (
        rows.rows[-1]["usd"] == rows.rows[-1]["input_tokens"] == rows.rows[-1]["output_tokens"] == 0
    )


def test_w6_unreachable_local_endpoint_cannot_mask_rejected_fallback(monkeypatch):
    monkeypatch.setenv("CAIRNWORK_LOCAL_RELAY", "1")
    calls = []

    def unreachable(_cap):
        calls.append(_cap)
        raise EndpointUnreachable("endpoint down")

    outcome = run_relay(
        "task",
        unreachable,
        lambda _: Acceptance(False, "FAILED"),
        CostGate(FakeTransport()),
        lambda _task: Attempt("agy-candidate", "agy"),
        Rows(),
        RelayConfig(enabled=True),
    )
    assert calls == [32768, 65536]
    assert outcome.route == "FAILED" and outcome.acceptance_passed is False


def test_default_jsonl_ledger_path_is_configured_and_ledger_appends(tmp_path):
    path = Path(DEFAULT_LEDGER_PATH).expanduser()
    assert path == Path.home() / ".local/state/cairnwork-local-relay/ledger.jsonl"
    ledger = JsonlLedger(tmp_path / "ledger.jsonl")
    ledger.append({"task_id": "fixture"})
    assert json.loads(ledger.path.read_text())["task_id"] == "fixture"
