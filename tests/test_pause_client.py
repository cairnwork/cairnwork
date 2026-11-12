"""Configured PAUSE clients and read-only gate replay (#476 step 2)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from experimental.pause import cli, schema
from experimental.pause.client import HaikuGate, HaikuPlanner, TransportResult
from experimental.pause.interfaces import CallSettings, GateRequest, PlanRequest
from experimental.pause.secrets_scan import Scanner


class FakeTransport:
    def __init__(self, *results: TransportResult):
        self.results = list(results)
        self.calls = []

    def send(self, model, effort, messages, timeout):
        self.calls.append((model, effort, messages, timeout))
        return self.results.pop(0) if len(self.results) > 1 else self.results[0]


SETTINGS = CallSettings("haiku-test", "high", 3.0, 3)


def plan_request(messages=({"role": "user", "content": "task"},), settings=SETTINGS):
    return PlanRequest("t", "c", messages, settings)


def gate_request(plan="do the work", settings=SETTINGS):
    return GateRequest("t", "c", plan, settings)


@pytest.mark.parametrize(
    "kind,status,body,reason",
    [
        ("timeout", 0, None, schema.Reason.PAUSE_PLANNER_TIMEOUT),
        ("401", 401, {}, schema.Reason.PAUSE_PLANNER_REFUSED),
        ("429", 429, {}, schema.Reason.PAUSE_PLANNER_REFUSED),
        ("5xx", 500, {}, schema.Reason.PAUSE_PLANNER_REFUSED),
        ("refusal", 200, {"stop_reason": "refusal"}, schema.Reason.PAUSE_PLANNER_REFUSAL),
        ("bad", 200, {"text": "  "}, schema.Reason.PAUSE_PLANNER_BAD_OUTPUT),
        ("quota", 0, None, schema.Reason.PAUSE_PLANNER_QUOTA_EXHAUSTED),
    ],
)
def test_planner_failure_mapping_and_sent_settings(kind, status, body, reason):
    result = TransportResult(
        status, body, timed_out=kind == "timeout", quota_blocked=kind == "quota"
    )
    transport = FakeTransport(result)
    reply = HaikuPlanner(transport, Scanner()).plan(plan_request())
    assert reply.reason == reason and reply.text is None
    assert (reply.model_id, reply.effort) == (SETTINGS.model_id, SETTINGS.effort)
    assert transport.calls[0][0:2] == (SETTINGS.model_id, SETTINGS.effort)


@pytest.mark.parametrize(
    "kind,status,body,reason",
    [
        ("timeout", 0, None, schema.Reason.PAUSE_GATE_TIMEOUT),
        ("401", 401, {}, schema.Reason.PAUSE_GATE_REFUSED),
        ("429", 429, {}, schema.Reason.PAUSE_GATE_REFUSED),
        ("5xx", 500, {}, schema.Reason.PAUSE_GATE_REFUSED),
        ("refusal", 200, {"stop_reason": "refusal"}, schema.Reason.PAUSE_GATE_REFUSAL),
        ("bad", 200, {"text": "prefix ACCEPT"}, schema.Reason.PAUSE_GATE_BAD_OUTPUT),
        ("quota", 0, None, schema.Reason.PAUSE_GATE_QUOTA_EXHAUSTED),
    ],
)
def test_gate_failure_mapping(kind, status, body, reason):
    transport = FakeTransport(
        TransportResult(status, body, timed_out=kind == "timeout", quota_blocked=kind == "quota")
    )
    reply = HaikuGate(transport).review(gate_request())
    assert reply.reason == reason and reply.verdict is None
    assert (reply.model_id, reply.effort) == (SETTINGS.model_id, SETTINGS.effort)
    assert transport.calls[0][0:2] == (SETTINGS.model_id, SETTINGS.effort)


@pytest.mark.parametrize("bad", ["", "accept", " ACCEPTED", "prefix\nREJECT", "\n"])
def test_gate_rejects_noncanonical_first_line(bad):
    reply = HaikuGate(FakeTransport(TransportResult(200, {"text": bad}))).review(gate_request())
    assert reply.reason == schema.Reason.PAUSE_GATE_BAD_OUTPUT


@pytest.mark.parametrize("status,timed_out", [(500, False), (0, True)])
@pytest.mark.parametrize("attempts", [1, 2, 3])
def test_retry_count_is_bounded(status, timed_out, attempts):
    settings = CallSettings("h", "low", 1, attempts)
    fake = FakeTransport(
        *(TransportResult(status, {}, timed_out=timed_out) for _ in range(attempts))
    )
    reply = HaikuPlanner(fake).plan(plan_request(settings=settings))
    assert len(fake.calls) == attempts
    assert reply.reason == (
        schema.Reason.PAUSE_PLANNER_TIMEOUT if timed_out else schema.Reason.PAUSE_PLANNER_REFUSED
    )


@pytest.mark.parametrize("status", [401, 429, 200])
def test_nonretryable_results_are_sent_once(status):
    body = {"text": "plan"} if status == 200 else {}
    fake = FakeTransport(TransportResult(status, body))
    HaikuPlanner(fake).plan(plan_request())
    assert len(fake.calls) == 1


@pytest.mark.parametrize(
    "secret",
    [
        "sk-abcdefghijk12345",
        "sk-ant-abcdefghijk12345",
        "ghp_abcdefghijk12345",
        "github_pat_abcdefghijk12345",
        "AKIA1234567890ABCDEF",
        "xoxb-123456789012345678",
        "-----BEGIN RSA PRIVATE KEY-----",
        "Bearer abcdefghijklmnopqrstuvwxyz012345",
        "secret=" + "a" * 45,
        "/Users/alice/private/file",
        "/home/alice/private/file",
        "a@b.example",
    ],
)
def test_planner_secrets_block_transport(secret):
    fake = FakeTransport()
    reply = HaikuPlanner(fake).plan(plan_request(({"content": secret},)))
    assert fake.calls == [] and reply.reason == schema.Reason.PAUSE_SECRET_BLOCKED


def test_gate_secret_blocks_transport():
    fake = FakeTransport()
    reply = HaikuGate(fake).review(gate_request("token=" + "b" * 45))
    assert fake.calls == [] and reply.reason == schema.Reason.PAUSE_SECRET_BLOCKED


def _tree(root: Path):
    return sorted(
        (str(p.relative_to(root)), p.read_bytes()) for p in root.rglob("*") if p.is_file()
    )


def test_gate_replay_is_read_only_and_uses_checkpoint_or_plan_hash(tmp_path):
    candidate = tmp_path / "candidates"
    task = candidate / "one"
    task.mkdir(parents=True)
    (task / "plan.txt").write_text("plan", encoding="utf-8")
    (task / "expected_verdict.txt").write_text("ACCEPT", encoding="utf-8")
    before = _tree(tmp_path)
    transport = FakeTransport(TransportResult(200, {"text": "ACCEPT\nlooks good"}))
    gate = HaikuGate(transport)
    code = cli.main(
        ["gate-replay", str(candidate), "--gate-config", "unused"],
        gate_factory=lambda _: (gate, SETTINGS),
    )
    assert code == 0
    assert _tree(tmp_path) == before
    request_text = transport.calls[0][2][0]["content"]
    assert request_text == "plan"
    assert hashlib.sha256(b"plan").hexdigest()
    assert not (tmp_path / "run_root").exists()
    assert not (tmp_path / schema.RECEIPTS_FILE).exists()


def test_no_agy_dispatch_reference():
    package = Path(__file__).parents[1] / "experimental" / "pause"
    assert "agy_dispatch" not in "\n".join(
        p.read_text(encoding="utf-8") for p in package.glob("*.py")
    )
