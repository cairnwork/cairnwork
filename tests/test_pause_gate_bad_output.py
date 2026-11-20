"""Bad gate output retry and status diagnostics."""

from experimental.pause import schema
from experimental.pause.client import HaikuGate, TransportResult
from experimental.pause.interfaces import CallSettings, GateRequest


class FakeTransport:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    def send(self, model, effort, messages, timeout):
        self.calls.append((model, effort, messages, timeout))
        return self.results.pop(0)


SETTINGS = CallSettings("haiku-test", "high", 3.0, 1)


def _request():
    return GateRequest("task", "digest", "plan", SETTINGS)


def test_empty_gate_reply_retries_once_then_accepts():
    transport = FakeTransport(
        TransportResult(204, {"text": ""}),
        TransportResult(200, {"text": "ACCEPT"}),
    )
    reply = HaikuGate(transport).review(_request())
    assert reply.verdict == "ACCEPT" and reply.reason is None
    assert len(transport.calls) == 2


def test_two_empty_gate_replies_fail_and_record_last_http_status():
    transport = FakeTransport(
        TransportResult(204, {"text": ""}),
        TransportResult(206, {"text": ""}),
    )
    reply = HaikuGate(transport).review(_request())
    assert reply.reason == schema.Reason.PAUSE_GATE_BAD_OUTPUT
    assert len(transport.calls) == 2
    assert reply.diagnostics == ("HTTP 206",)
