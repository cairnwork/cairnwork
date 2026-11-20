"""Failure evidence: Core refusal reason, secret-scan hit categories, gate failure kind."""

from __future__ import annotations

import json

from experimental.local_relay.adapters import CoreAcceptance
from experimental.local_relay.evidence import RecordingScanner, RecordingVerify, summarize_result
from experimental.local_relay.proxy_gate import ProxyGate
from experimental.pause.flow import RunResult
from experimental.pause.interfaces import CallSettings, GateRequest
from experimental.pause.schema import Reason


def test_summary_keeps_reason_and_log_tail_redacted():
    secret = "ghp_" + "a" * 30
    result = {
        "status": "REFUSED", "reason": f"TEST_COMMIT_ALREADY_EXPOSED token {secret}",
        "logs": [f"line{i}" for i in range(100)] + [f"Authorization: Bearer {'x' * 30}"],
        "uncomparable": None,
    }  # fmt: skip
    summary = summarize_result(result)
    assert summary["status"] == "REFUSED" and "TEST_COMMIT_ALREADY_EXPOSED" in summary["reason"]
    assert secret not in json.dumps(summary) and "x" * 30 not in summary["logs_tail"]
    assert "line99" in summary["logs_tail"] and "line0\n" not in summary["logs_tail"]


def test_recording_verify_clears_between_calls():
    answers = iter([{"status": "FAILED", "reason": "r1"}, RuntimeError("boom")])

    def inner(_intake, **_kw):
        value = next(answers)
        if isinstance(value, Exception):
            raise value
        return value

    rec = RecordingVerify(inner)
    rec(None)
    assert rec.last["reason"] == "r1"
    try:
        rec(None)
    except RuntimeError:
        pass
    assert rec.last is None  # a stale summary is never attached to the next stage


def test_acceptance_carries_detail_without_changing_the_verdict(tmp_path):
    class Verdict:
        status, tree_sha256 = "REFUSED", "t" * 64

    acceptance = CoreAcceptance(lambda _r: Verdict(), detail=lambda: {"reason": "why"})
    result = acceptance(RunResult(0, "", tmp_path, tmp_path, base_sha="a" * 40))
    assert result.passed is False and result.detail == {"reason": "why"}
    assert json.loads(result.raw) == {"status": "REFUSED", "tree_sha256": "t" * 64}


def test_scanner_records_categories_and_lines_not_text():
    scanner = RecordingScanner()
    text = "ok line\nmy_token = 'abcdefghijklmnop1234'\nfine\n"
    hits = scanner.scan(text)
    assert hits and scanner.last["categories"] and scanner.last["hit_lines"] == [2]
    assert "abcdefghijklmnop1234" not in json.dumps(scanner.last)


def test_scanner_error_is_recorded_and_still_raised():
    class Broken:
        def scan(self, _text):
            raise ValueError("secret-ish message")

    scanner = RecordingScanner(Broken())
    try:
        scanner.scan("x")
    except ValueError:
        pass
    else:
        raise AssertionError("must still fail closed")
    assert scanner.last == {"scanner_error": "ValueError"}


def _gate(replies):
    calls = []

    def request(method, url, body):
        if "/stats" in url:
            return {"cost_usd": 0.01, "input_tokens": 10, "output_tokens": 5}
        if url.endswith("/label"):
            return {}
        calls.append(body)
        value = next(replies)
        if isinstance(value, Exception):
            raise value
        return value

    return ProxyGate("http://127.0.0.1:18301/v1", request), calls


def _req(plan="plan"):
    return GateRequest("t" * 8, "", plan, CallSettings("haiku", "medium", 60.0, 1))


def _chat(text, finish="stop"):
    return {
        "choices": [{"message": {"content": text}, "finish_reason": finish}],
        "usage": {"completion_tokens": 7},
    }


def test_gate_retries_once_for_an_unusable_reply_then_accepts():
    gate, calls = _gate(iter([_chat(""), _chat("可直接合併")]))
    reply = gate.review(_req())
    assert reply.verdict == "ACCEPT" and len(calls) == 2
    assert reply.detail["transport"]["attempts"] == 2


def test_gate_bad_output_keeps_failure_kind_and_finish_reason():
    gate, calls = _gate(iter([_chat("", "length"), _chat("???", "length")]))
    reply = gate.review(_req())
    assert reply.reason == Reason.PAUSE_GATE_BAD_OUTPUT and len(calls) == 2
    transport = reply.detail["transport"]
    assert transport["failure"] == "no_verdict" and transport["finish_reason"] == "length"


def test_gate_http_error_is_recorded_after_retry():
    gate, _calls = _gate(iter([RuntimeError("proxy returned HTTP 502")] * 2))
    reply = gate.review(_req())
    assert reply.verdict is None
    assert "RuntimeError" in reply.detail["transport"]["failure"]
    assert "HTTP 502" in reply.detail["transport"]["error"]


def test_secret_block_records_scan_categories_and_sends_nothing():
    gate, calls = _gate(iter([]))
    reply = gate.review(_req("see /Users/someone/project and my_token = 'abcdefghijklmnop1234'"))
    assert reply.reason == Reason.PAUSE_SECRET_BLOCKED and calls == []
    assert "host_path" in reply.detail["scan"]["categories"]
