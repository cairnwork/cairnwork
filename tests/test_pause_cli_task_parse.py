"""PAUSE derives task identity using the worker parser's argparse behavior."""

import json
from typing import Any, cast

import pytest

from experimental.pause import cli
from experimental.pause.flow import RunResult


@pytest.mark.parametrize("spelling", ["space", "equals", "prefix", "repeated"])
def test_configured_run_uses_worker_selected_task(tmp_path, monkeypatch, spelling):
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    first.write_text(json.dumps({"task_id": "first"}))
    second.write_text(json.dumps({"task_id": "second"}))
    args = ["run", "--planner-config", "config"]
    if spelling == "space":
        args += ["--task", str(second)]
    elif spelling == "equals":
        args += [f"--task={second}"]
    elif spelling == "prefix":
        args += ["--tas", str(second)]
    else:
        args += ["--task", str(first), "--task", str(second)]
    args += [
        "--checkout",
        str(tmp_path / "checkout"),
        "--model",
        "m",
        "--context-cap",
        "1",
        "--canary-report",
        str(tmp_path / "canary"),
        "--out",
        str(tmp_path / "out"),
    ]
    captured = {}

    def fake_flow(worker_args, **kwargs):
        captured["task_id"] = kwargs["task_id"]
        return RunResult(0, "ok", tmp_path, tmp_path), {"receipt": True}

    monkeypatch.setattr(cli, "run_flow", fake_flow)
    monkeypatch.setattr(cli.receipt_module, "append", lambda *_: None)
    monkeypatch.setattr(cli.receipt_module, "next_event_seq", lambda _: 0)
    assert (
        cli.main(args, run_factory=cast(Any, lambda _: (None, None, None, None, None, None))) == 0
    )
    assert captured["task_id"] == "second"
