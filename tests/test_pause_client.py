"""Configured PAUSE clients and read-only gate replay (#476 step 2)."""

from __future__ import annotations

import hashlib
import json
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
        "AWS_SECRET_ACCESS_KEY=" + "a" * 20,
        "APIKEY=" + "a" * 20,
        "accessToken=" + "a" * 20,
        'clientSecret: "' + "a" * 20 + '"',
        "secretAccessKey=" + "a" * 20,
        "authToken=" + "a" * 20,
        "privateKey=" + "a" * 20,
        "my-api-key=" + "a" * 20,
        "GITHUB_TOKEN=" + "a" * 20,
        "client_secret=" + "a" * 20,
        'key: "' + "a" * 20 + '"',
        '{"token": "' + "a" * 20 + '"}',
        "gho_abcdefghijk12345",
        "ghs_abcdefghijk12345",
        "ghu_abcdefghijk12345",
        "ghr_abcdefghijk12345",
        "xoxa-123456789012345678",
        "xoxs-123456789012345678",
        "xoxe-123456789012345678",
        "AIza" + "a" * 20,
        "/root/.ssh/id",
        "/users/alice/x",
        "C:\\Users\\alice\\x",
        "/Users/alice/private/file",
        "/home/alice/private/file",
        "a@b.example",
    ],
)
def test_planner_secrets_block_transport(secret):
    fake = FakeTransport()
    reply = HaikuPlanner(fake).plan(plan_request(({"content": secret},)))
    assert fake.calls == [] and reply.reason == schema.Reason.PAUSE_SECRET_BLOCKED


@pytest.mark.parametrize(
    "messages",
    [
        ({"sk-abcdefghijk12345": "x"},),  # in a key
        ({"content": b"sk-abcdefghijk12345"},),  # bytes
        ({"content": [{"text": ["nested", "/Users/alice/x"]}]},),  # nested
    ],
)
def test_planner_secret_evasions_block_transport(messages):
    fake = FakeTransport()
    reply = HaikuPlanner(fake).plan(plan_request(messages))
    assert fake.calls == [] and reply.reason == schema.Reason.PAUSE_SECRET_BLOCKED


def test_adjacent_fields_are_not_glued_into_a_false_secret():
    fake = FakeTransport(TransportResult(200, {"text": "plan"}))
    messages = ({"a": "ends with sk-", "b": "abcdefghijk12345 starts"},)
    assert HaikuPlanner(fake).plan(plan_request(messages)).text == "plan"


def test_non_string_scalars_do_not_crash_scan():
    fake = FakeTransport(TransportResult(200, {"text": "plan"}))
    reply = HaikuPlanner(fake).plan(plan_request(({"content": "ok", "n": 3, "none": None},)))
    assert reply.text == "plan"


@pytest.mark.parametrize("status", [600, 302, 0, -1, 404])
def test_non_5xx_statuses_are_sent_once_and_refused(status):
    fake = FakeTransport(TransportResult(status, {}))
    reply = HaikuPlanner(fake).plan(plan_request())
    assert len(fake.calls) == 1 and reply.reason == schema.Reason.PAUSE_PLANNER_REFUSED


@pytest.mark.parametrize("body", [{"refusal": "no", "text": "ACCEPT"}, {"stop_reason": "refusal"}])
def test_refusal_markers_never_yield_a_verdict(body):
    reply = HaikuGate(FakeTransport(TransportResult(200, body))).review(gate_request())
    assert reply.verdict is None and reply.reason == schema.Reason.PAUSE_GATE_REFUSAL


@pytest.mark.parametrize("status", [None, "200", {"a": 1}, True, 1.5])
def test_command_transport_bad_status_does_not_raise(status, tmp_path):
    import sys

    from experimental.pause.client import CommandTransport

    script = "import json,sys;sys.stdin.read();print(json.dumps({'status': %r, 'body': {}}))"
    transport = CommandTransport([sys.executable, "-c", script % (status,)])
    assert transport.send("m", "e", [], 5.0).status == -1


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
    assert not (tmp_path / "run_root").exists()
    assert not (tmp_path / schema.RECEIPTS_FILE).exists()


def test_no_agy_dispatch_reference():
    package = Path(__file__).parents[1] / "experimental" / "pause"
    assert "agy_dispatch" not in "\n".join(
        p.read_text(encoding="utf-8") for p in package.glob("*.py")
    )


def test_gate_replay_passes_checkpoint_hash_or_plan_hash(tmp_path):
    from experimental.pause.interfaces import GateReply
    from experimental.pause.replay import replay

    seen = []

    class RecordingGate:
        def review(self, request):
            seen.append(request)
            return GateReply("ACCEPT", request.settings.model_id, request.settings.effort)

    for name, checkpoint in (("a", "c" * 64), ("b", None)):
        task = tmp_path / name
        task.mkdir()
        (task / "plan.txt").write_text("plan " + name, encoding="utf-8")
        if checkpoint:
            (task / "checkpoint.sha256").write_text(checkpoint + "\n", encoding="utf-8")
    report = replay(tmp_path, RecordingGate(), SETTINGS)
    assert [r.task_id for r in report.results] == ["a", "b"] and report.agreement is None
    assert seen[0].checkpoint_sha256 == "c" * 64
    assert seen[1].checkpoint_sha256 == hashlib.sha256(b"plan b").hexdigest()


def test_command_transport_unusable_command_or_output_never_raises():
    import sys

    from experimental.pause.client import CommandTransport

    missing = CommandTransport(["/nonexistent/pause-transport"]).send("m", "e", [], 5.0)
    assert missing.status == -1 and missing.body is None
    unserializable = CommandTransport([sys.executable, "-c", "pass"]).send(
        "m", "e", [{1: object()}], 5.0
    )
    assert unserializable.status == -1
    garbage = CommandTransport([sys.executable, "-c", "print('not json')"]).send("m", "e", [], 5.0)
    reply = HaikuPlanner(FakeTransport(garbage)).plan(plan_request())
    assert reply.reason == schema.Reason.PAUSE_PLANNER_BAD_OUTPUT


@pytest.mark.parametrize("text", ["/Users/alice", "cd /home/bob", "/Users/alice\n"])
def test_host_path_without_trailing_slash_is_blocked(text):
    fake = FakeTransport()
    reply = HaikuPlanner(fake).plan(plan_request(({"content": text},)))
    assert fake.calls == [] and reply.reason == schema.Reason.PAUSE_SECRET_BLOCKED


def test_configured_account_name_blocks_transport():
    fake = FakeTransport()
    scanner = Scanner(("jdoe",))
    reply = HaikuPlanner(fake, scanner).plan(plan_request(({"content": "owner is JDoe here"},)))
    assert fake.calls == [] and reply.reason == schema.Reason.PAUSE_SECRET_BLOCKED
    ok = HaikuPlanner(FakeTransport(TransportResult(200, {"text": "p"})), scanner)
    assert ok.plan(plan_request(({"content": "jdoes and ajdoe"},))).text == "p"


def test_replay_agreement_value(tmp_path):
    from experimental.pause.interfaces import GateReply
    from experimental.pause.replay import replay

    class Gate:
        def review(self, request):
            verdict = "ACCEPT" if request.task_id in ("a", "b") else "REJECT"
            return GateReply(verdict, request.settings.model_id, request.settings.effort)

    for name, expected in (("a", "ACCEPT"), ("b", "REJECT"), ("c", "REJECT"), ("d", "ACCEPT")):
        task = tmp_path / name
        task.mkdir()
        (task / "plan.txt").write_text("p", encoding="utf-8")
        (task / "expected_verdict.txt").write_text(expected, encoding="utf-8")
    assert replay(tmp_path, Gate(), SETTINGS).agreement == 0.5  # a and c match, b and d do not


@pytest.mark.parametrize("attempts", [1, 2, 3])
def test_retry_count_is_exactly_max_attempts(attempts):
    fake = FakeTransport(TransportResult(500, {}))
    HaikuGate(fake).review(gate_request(settings=CallSettings("m", "e", 1.0, attempts)))
    assert len(fake.calls) == attempts


@pytest.mark.parametrize("command", ["abc", [], [1, 2], None])
def test_gate_config_transport_command_must_be_a_string_list(command, tmp_path):
    config = tmp_path / "g.json"
    config.write_text(
        json.dumps(
            {
                "model_id": "m",
                "effort": "e",
                "timeout_seconds": 1,
                "max_attempts": 1,
                "transport_command": command,
            }
        ),
        encoding="utf-8",
    )
    assert cli.main(["gate-replay", str(tmp_path), "--gate-config", str(config)]) == 2


@pytest.mark.parametrize(
    "text",
    [
        "monkey = this_is_a_normal_long_variable",
        "monkey: this_is_a_normal_long_variable",
        "turkey = another_very_long_identifier_name",
        "token = next_token()",
        "password: str",
    ],
)
def test_ordinary_text_with_key_like_words_is_not_blocked(text):
    fake = FakeTransport(TransportResult(200, {"text": "plan"}))
    assert HaikuPlanner(fake).plan(plan_request(({"content": text},))).text == "plan"


def test_gate_replay_end_to_end_blocks_configured_account_name(tmp_path, capsys):
    import sys

    candidates = tmp_path / "c"
    for name, plan in (("blocked", "ask jdoe for the file"), ("fine", "edit the parser")):
        task = candidates / name
        task.mkdir(parents=True)
        (task / "plan.txt").write_text(plan, encoding="utf-8")
    script = (
        "import json,sys;sys.stdin.read();"
        "print(json.dumps({'status':200,'body':{'text':'ACCEPT'}}))"
    )
    config = tmp_path / "gate.json"
    config.write_text(
        json.dumps(
            {
                "model_id": "m",
                "effort": "e",
                "timeout_seconds": 5,
                "max_attempts": 1,
                "transport_command": [sys.executable, "-c", script],
                "scan_names": ["jdoe"],
            }
        ),
        encoding="utf-8",
    )
    assert cli.main(["gate-replay", str(candidates), "--gate-config", str(config)]) == 0
    results = {r["task_id"]: r for r in json.loads(capsys.readouterr().out)["results"]}
    assert results["blocked"]["verdict"] is None
    assert results["blocked"]["reason"] == schema.Reason.PAUSE_SECRET_BLOCKED.value
    assert results["fine"]["verdict"] == "ACCEPT" and results["fine"]["reason"] is None

