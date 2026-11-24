"""Pin #1546: PAUSE secret-block diagnostics (category, de-duplication, hand-off) (#1574)."""

from __future__ import annotations

from experimental.local_relay.adapters import CostGate
from experimental.local_relay.proxy_gate import ProxyGate
from experimental.pause import schema
from experimental.pause.client import HaikuGate, HaikuPlanner, TransportResult
from experimental.pause.interfaces import CallSettings, GateRequest, PlanRequest
from experimental.pause.secrets_scan import Scanner

SETTINGS = CallSettings("haiku-test", "high", 3.0, 3)


class _Scripted(Scanner):
    """Scanner stub: returns fixed categories per piece, or raises."""

    def __init__(self, by_text=None, error=False):
        super().__init__()
        self.by_text = by_text or {}
        self.error = error

    def scan(self, text):
        if self.error:
            raise RuntimeError("boom with ghp_abcdefghijk12345")
        return list(self.by_text.get(text, []))


class _Transport:
    def __init__(self):
        self.calls = 0

    def send(self, model, effort, messages, timeout):
        self.calls += 1
        return TransportResult(200, {"text": "ACCEPT"})


def _gate_request():
    return GateRequest("t", "c", "plan-text", SETTINGS)


def test_scanner_error_category_is_exact_literal():
    reply = HaikuGate(_Transport(), _Scripted(error=True)).review(_gate_request())
    assert reply.reason == schema.Reason.PAUSE_SECRET_BLOCKED
    assert reply.diagnostics == ("scanner_error",)
    assert "ghp_abcdefghijk12345" not in repr(reply)


def test_scanner_error_category_on_planner_too():
    request = PlanRequest("t", "c", ({"content": "x"},), SETTINGS)
    reply = HaikuPlanner(_Transport(), _Scripted(error=True)).plan(request)
    assert reply.diagnostics == ("scanner_error",)


def test_categories_are_deduplicated_in_first_seen_order():
    scanner = _Scripted({"a": ["api_key", "account_name"], "b": ["api_key", "private_key"]})
    transport = _Transport()
    request = PlanRequest("t", "c", ({"content": "a"}, {"content": "b"}), SETTINGS)
    reply = HaikuPlanner(transport, scanner).plan(request)
    assert transport.calls == 0
    assert reply.diagnostics == ("api_key", "account_name", "private_key")


def test_same_category_from_many_pieces_appears_once():
    scanner = _Scripted({"a": ["api_key"], "b": ["api_key"]})
    request = PlanRequest("t", "c", ({"content": "a"}, {"content": "b"}), SETTINGS)
    assert HaikuPlanner(_Transport(), scanner).plan(request).diagnostics == ("api_key",)


def _proxy_request(stats_ok):
    def request(method, url, payload):
        if method == "GET":
            if not stats_ok:
                raise OSError("stats down")
            return {
                "cost_usd": 0.01,
                "input_tokens": 5,
                "output_tokens": 2,
                "budget_stop": False,
                "model_mismatch": False,
                "requests": 1,
            }
        return {}

    return request


def test_proxy_gate_hands_off_diagnostics_when_metered():
    scanner = _Scripted({"plan-text": ["account_name"]})
    gate = ProxyGate("http://127.0.0.1:9/v1", _proxy_request(True), scanner)
    reply = gate.review(_gate_request())
    assert reply.reason == schema.Reason.PAUSE_SECRET_BLOCKED
    assert reply.diagnostics == ("account_name",)


def test_proxy_gate_hands_off_diagnostics_when_unmetered():
    scanner = _Scripted({"plan-text": ["account_name"]})
    gate = ProxyGate("http://127.0.0.1:9/v1", _proxy_request(False), scanner)
    reply = gate.review(_gate_request())
    assert reply.reason == schema.Reason.PAUSE_SECRET_BLOCKED
    assert reply.diagnostics == ("account_name",)


def test_cost_gate_hands_off_diagnostics():
    scanner = _Scripted({"plan-text": ["api_key"]})
    reply = CostGate(_Transport(), scanner=scanner).review(_gate_request())
    assert reply.reason == schema.Reason.PAUSE_SECRET_BLOCKED
    assert reply.diagnostics == ("api_key",)
