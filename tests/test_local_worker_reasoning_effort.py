"""Optional reasoning effort for the experimental local worker."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest
from local_worker_fakes import cli_harness, lines, make_loop, reply

from experimental.local_worker.agent_protocol import tool_schemas
from experimental.local_worker.cli import parser


def test_payload_is_unchanged_without_reasoning_effort(tmp_path):
    loop, _, _, _ = make_loop(tmp_path, [reply("finish", status="DONE")])

    assert loop._payload([]) == {
        "model": "served-model",
        "messages": [],
        "tools": tool_schemas(True),
        "tool_choice": "auto",
        "parallel_tool_calls": False,
        "temperature": 0.0,
        "max_tokens": loop.limits.max_completion_tokens,
        "seed": 7,
    }


def test_payload_includes_reasoning_effort_when_set(tmp_path):
    loop, _, _, _ = make_loop(tmp_path, [reply("finish", status="DONE")])
    loop.settings = replace(loop.settings, reasoning_effort="high")

    assert loop._payload([])["reasoning_effort"] == "high"


def test_reasoning_effort_is_recorded_in_episode(tmp_path, monkeypatch):
    run, out = cli_harness(tmp_path, monkeypatch)

    assert run([reply("finish", status="DONE")], "run", "--reasoning-effort", "high") == 0
    assert lines(out)[0]["seed"] is None
    assert json.loads((out / "manifest.json").read_text())["reasoning_effort"] == "high"


def test_argparse_rejects_other_reasoning_effort_values(capsys):
    with pytest.raises(SystemExit) as exc:
        parser().parse_args(["run", "--reasoning-effort", "extreme"])

    assert exc.value.code == 2
    assert "invalid choice" in capsys.readouterr().err
