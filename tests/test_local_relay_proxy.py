"""Offline tests for the loopback Haiku proxy fallback."""

from __future__ import annotations

from pathlib import Path

import pytest

from experimental.local_relay.adapters import HaikuProxyFallback
from experimental.local_relay.config import RelayConfig
from experimental.local_relay.relay import Acceptance, LocalAttemptFailed, run_relay
from experimental.local_worker.endpoint import EndpointUnreachable
from experimental.pause.flow import RunResult
from experimental.pause.interfaces import GateReply, GateRequest


class Rows:
    def __init__(self):
        self.rows = []

    def append(self, row):
        self.rows.append(row)


class Gate:
    def review(self, request: GateRequest):
        del request
        return GateReply("ACCEPT", "haiku", "medium", None)


def _result(_context=65536, _endpoint="http://127.0.0.1:18301/v1"):
    path = Path(__file__).resolve().parent
    return RunResult(
        0,
        "complete",
        path,
        path,
        record={
            "verification": {"exit_reason": "COMPLETED"},
            "spec_feedback": {"needs_spec": False},
        },
    )


def _relay(fallback, rows):
    def unavailable(_context):
        raise EndpointUnreachable("local offline")

    return run_relay(
        "task-label",
        unavailable,
        lambda _candidate: Acceptance(True, "VERIFIED"),
        Gate(),
        fallback,
        rows,
        RelayConfig(enabled=True),
    )


def test_proxy_fallback_records_label_stats_and_cost(monkeypatch):
    monkeypatch.setenv("CAIRNWORK_LOCAL_RELAY", "1")
    observed = []
    stats = {
        "requests": 1,
        "ok": 1,
        "failed": 0,
        "cost_usd": 0.004,
        "input_tokens": 200,
        "output_tokens": 80,
        "budget_stop": False,
        "model_mismatch": False,
    }

    def request(method, url, body):
        observed.append((method, url, body))
        return stats

    fallback = HaikuProxyFallback(
        "http://127.0.0.1:18301/v1",
        lambda context, endpoint: observed.append((context, endpoint)) or _result(),
        request=request,
    )
    rows = Rows()
    outcome = _relay(fallback, rows)
    assert outcome.route == "FALLBACK", outcome
    assert observed[0] == ("POST", "http://127.0.0.1:18301/ab/label", {"label": "task-label"})
    assert observed[-2] == ("POST", "http://127.0.0.1:18301/ab/label", {"label": "idle"})
    assert observed[-1][1].endswith("/ab/stats?label=task-label")
    row = next(row for row in rows.rows if row["stage"] == "fallback")
    assert (row["model_id"], row["usd"], row["input_tokens"], row["output_tokens"]) == (
        "claude-haiku-5-5",
        0.004,
        200,
        80,
    )


@pytest.mark.parametrize("failure", ["stats", "budget"])
def test_proxy_stats_failure_or_budget_stop_fails_fallback(monkeypatch, failure):
    monkeypatch.setenv("CAIRNWORK_LOCAL_RELAY", "1")
    stats = {
        "requests": 1,
        "ok": 1,
        "failed": 0,
        "cost_usd": 0.01,
        "input_tokens": 1,
        "output_tokens": 1,
        "budget_stop": failure == "budget",
        "model_mismatch": False,
    }

    def request(_method, url, _body):
        if failure == "stats" and "/stats?" in url:
            raise RuntimeError("stats unavailable")
        return stats

    fallback = HaikuProxyFallback("http://127.0.0.1:18301/v1", lambda *_: _result(), request)
    assert _relay(fallback, Rows()).route == "FAILED"


def test_proxy_label_failure_is_a_fallback_failure(monkeypatch):
    monkeypatch.setenv("CAIRNWORK_LOCAL_RELAY", "1")

    def request(*_args):
        raise RuntimeError("label unavailable")

    fallback = HaikuProxyFallback("http://127.0.0.1:18301/v1", lambda *_: _result(), request)
    assert _relay(fallback, Rows()).route == "FAILED"


def test_programming_errors_escape_fallback(monkeypatch):
    monkeypatch.setenv("CAIRNWORK_LOCAL_RELAY", "1")

    def broken(*_args):
        raise TypeError("programming bug")

    fallback = HaikuProxyFallback("http://127.0.0.1:18301/v1", broken, lambda *_: {})
    with pytest.raises(TypeError, match="programming bug"):
        _relay(fallback, Rows())


def test_incomplete_proxy_episode_is_explicit_fallback_failure():
    fallback = HaikuProxyFallback(
        "http://127.0.0.1:18301/v1",
        lambda *_: RunResult(
            1, "incomplete", Path(__file__).resolve().parent, Path(__file__).resolve().parent
        ),
        lambda *_: {},
    )
    with pytest.raises(LocalAttemptFailed, match="process_exit=1"):
        fallback("task-label")


def test_proxy_stats_accept_bool_or_int_flags_and_reject_nonzero_or_bad_types():
    from experimental.local_relay.adapters import _flag_set, _is_flag

    assert _is_flag(False) and _is_flag(True) and _is_flag(0) and _is_flag(2)
    assert not _flag_set(False) and not _flag_set(0)
    assert _flag_set(True) and _flag_set(1) and _flag_set(3)
    assert not _is_flag("0") and not _is_flag(None) and not _is_flag(-1) and not _is_flag(0.0)
