"""RELAY_GATE_MAX_TOKENS: experiment-only override of the gate reply budget (default unchanged)."""

import pytest

from experimental.local_relay import proxy_gate


def test_default_budget_is_unchanged(monkeypatch):
    monkeypatch.delenv("RELAY_GATE_MAX_TOKENS", raising=False)
    assert proxy_gate.gate_max_tokens() == 2048


def test_env_overrides_the_budget(monkeypatch):
    monkeypatch.setenv("RELAY_GATE_MAX_TOKENS", "8192")
    assert proxy_gate.gate_max_tokens() == 8192


@pytest.mark.parametrize("bad", ["", "abc", "0", "-5", "100", "999999", "8192.5", "True"])
def test_bad_values_fall_back_to_the_default(monkeypatch, bad):
    monkeypatch.setenv("RELAY_GATE_MAX_TOKENS", bad)
    assert proxy_gate.gate_max_tokens() == 2048
