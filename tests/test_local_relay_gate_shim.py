"""The gate shim speaks the PAUSE CommandTransport protocol over claude-api-run."""

import json
import stat
import sys

import pytest

from experimental.local_relay import gate_shim
from experimental.local_relay.adapters import CostGate
from experimental.pause.interfaces import CallSettings, GateRequest


def _runner(tmp_path, body, rc=0):
    script = tmp_path / "fake-claude-api-run"
    script.write_text(
        "#!/bin/sh\n"
        'echo "$@" > "$0.argv"\n'
        'cat > "$0.stdin"\n'
        f"cat <<'EOF'\n{body}\nEOF\n"
        f"exit {rc}\n",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return script


def _request():
    return {
        "model": "haiku",
        "effort": "medium",
        "messages": [{"role": "user", "content": "review"}],
    }


def test_success_maps_text_usage_and_reported_cost(tmp_path):
    body = json.dumps(
        {
            "type": "result",
            "result": "ACCEPT\nlooks right",
            "total_cost_usd": 0.0123,
            "usage": {"input_tokens": 321, "output_tokens": 45},
        }
    )
    runner = _runner(tmp_path, body)
    env = gate_shim.envelope(_request(), "cw-local-relay", str(runner), 30)
    assert env["status"] == 200
    assert env["body"]["text"].startswith("ACCEPT")
    assert env["body"]["usage"] == {"input_tokens": 321, "output_tokens": 45}
    assert env["body"]["cost_usd"] == 0.0123
    argv = (tmp_path / "fake-claude-api-run.argv").read_text()
    assert argv.split()[:3] == ["--tag", "cw-local-relay", "--"]
    assert "claude -p --model haiku --output-format json" in argv
    assert (tmp_path / "fake-claude-api-run.stdin").read_text() == "review"


def test_budget_gate_exit_is_quota_blocked_not_a_success(tmp_path):
    env = gate_shim.envelope(_request(), "t", str(_runner(tmp_path, "", rc=3)), 30)
    assert env == {"status": 0, "quota_blocked": True}


@pytest.mark.parametrize(
    "body, rc",
    [("not json", 0), ('{"type": "result", "is_error": true, "result": "x"}', 0), ("{}", 1)],
)
def test_bad_outputs_and_errors_are_not_successes(tmp_path, body, rc):
    env = gate_shim.envelope(_request(), "t", str(_runner(tmp_path, body, rc)), 30)
    assert env["status"] == -1 and "body" not in env


def test_missing_runner_is_a_refusal_not_a_crash(tmp_path):
    env = gate_shim.envelope(_request(), "t", str(tmp_path / "absent"), 30)
    assert env == {"status": -1}


def test_costgate_through_the_shim_reports_real_tokens_and_cost(tmp_path, monkeypatch):
    body = json.dumps(
        {
            "type": "result",
            "result": "ACCEPT",
            "total_cost_usd": 0.0042,
            "usage": {"input_tokens": 1000, "output_tokens": 10},
        }
    )
    runner = _runner(tmp_path, body)
    gate = CostGate(
        command=(
            sys.executable,
            "-m",
            "experimental.local_relay.gate_shim",
            "--tag",
            "cw-local-relay",
            "--runner",
            str(runner),
        )
    )
    settings = CallSettings("haiku", "medium", 60.0, 1)
    reply = gate.review(GateRequest("task", "", "candidate text", settings))
    assert reply.verdict == "ACCEPT" and reply.reason is None
    assert (reply.input_tokens, reply.output_tokens, reply.usd) == (1000, 10, 0.0042)


def test_costgate_surfaces_a_refusal_with_zero_cost(tmp_path):
    gate = CostGate(
        command=(
            sys.executable,
            "-m",
            "experimental.local_relay.gate_shim",
            "--tag",
            "t",
            "--runner",
            str(_runner(tmp_path, "boom", rc=3)),
        )
    )
    reply = gate.review(GateRequest("task", "", "c", CallSettings("haiku", "medium", 60.0, 1)))
    assert reply.verdict is None and reply.reason is not None and reply.usd == 0.0
