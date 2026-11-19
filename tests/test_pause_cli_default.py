"""Default configured CLI construction fails closed without a Core judge."""

import json
from pathlib import Path
from typing import Any

from experimental.pause import cli


def config(tmp_path: Path, core: dict[str, Any] | None = None) -> Path:
    client = {
        "model_id": "model",
        "effort": "low",
        "timeout_seconds": 1,
        "max_attempts": 1,
        "transport_command": ["transport"],
    }
    body: dict[str, Any] = {"planner": client, "gate": client, "runs_dir": str(tmp_path / "runs")}
    if core is not None:
        body["core"] = core
    path = tmp_path / "pause.json"
    path.write_text(json.dumps(body))
    return path


def test_default_config_without_core_passes_no_judge(tmp_path: Path, monkeypatch: Any) -> None:
    task = tmp_path / "task.json"
    task.write_text('{"task_id":"task"}')
    cfg = config(tmp_path)
    observed: dict[str, Any] = {}

    class Result:
        output = "fallback\n"
        code = 0

    def flow(_args: list[str], **kwargs: Any) -> tuple[Any, dict[str, bool]]:
        observed["judge"] = kwargs.get("core_verdict")
        return Result(), {"ok": True}

    monkeypatch.setattr(cli, "run_flow", flow)
    monkeypatch.setattr(cli.receipt_module, "append", lambda *_args: None)
    argv = [
        "run",
        "--planner-config",
        str(cfg),
        "--task",
        str(task),
        "--checkout",
        str(tmp_path / "r" / "checkout"),
        "--model",
        "m",
        "--context-cap",
        "1",
        "--canary-report",
        "c",
    ]
    assert cli.main(argv, worker_main=lambda _args: 99) == 0
    assert observed["judge"] is None


def test_malformed_default_config_exits_before_worker(tmp_path: Path) -> None:
    cfg = tmp_path / "bad.json"
    cfg.write_text("{")
    called: list[list[str]] = []
    argv = [
        "run",
        "--planner-config",
        str(cfg),
        "--task",
        "missing",
        "--checkout",
        "x",
        "--model",
        "m",
        "--context-cap",
        "1",
        "--canary-report",
        "c",
    ]
    assert cli.main(argv, worker_main=lambda args: called.append(args) or 0) == 2
    assert called == []


def test_configured_runner_uses_parsed_runs_when_config_omits_override(
    tmp_path: Path, monkeypatch: Any
) -> None:
    task = tmp_path / "task.json"
    task.write_text('{"task_id":"task"}')
    cfg = config(tmp_path)
    body = json.loads(cfg.read_text())
    body.pop("runs_dir")
    cfg.write_text(json.dumps(body))
    observed: dict[str, Any] = {}

    class Result:
        output = "ok\n"
        code = 0

    def flow(_args: list[str], **kwargs: Any) -> tuple[Any, dict[str, bool]]:
        observed["runs"] = kwargs["runner"].runs_dir
        return Result(), {"ok": True}

    monkeypatch.setattr(cli, "run_flow", flow)
    monkeypatch.setattr(cli.receipt_module, "append", lambda *_args: None)
    runs = tmp_path / "worker-runs"
    argv = [
        "run",
        "--planner-config",
        str(cfg),
        "--task",
        str(task),
        "--runs",
        str(runs),
        "--checkout",
        str(tmp_path / "checkout"),
        "--model",
        "m",
        "--context-cap",
        "1",
        "--canary-report",
        "c",
    ]
    assert cli.main(argv, worker_main=lambda _args: 99) == 0
    assert observed["runs"] == runs


def test_configured_runs_override_wins_over_worker_runs_arg(
    tmp_path: Path, monkeypatch: Any
) -> None:
    task = tmp_path / "task.json"
    task.write_text('{"task_id":"task"}')
    cfg = config(tmp_path)
    observed: dict[str, Any] = {}

    class Result:
        output = "ok\n"
        code = 0

    def flow(_args: list[str], **kwargs: Any) -> tuple[Any, dict[str, bool]]:
        observed["runs"] = kwargs["runner"].runs_dir
        return Result(), {"ok": True}

    monkeypatch.setattr(cli, "run_flow", flow)
    monkeypatch.setattr(cli.receipt_module, "append", lambda *_args: None)
    argv = [
        "run",
        "--planner-config",
        str(cfg),
        "--task",
        str(task),
        "--runs",
        str(tmp_path / "worker-runs"),
        "--checkout",
        str(tmp_path / "checkout"),
        "--model",
        "m",
        "--context-cap",
        "1",
        "--canary-report",
        "c",
    ]
    assert cli.main(argv, worker_main=lambda _args: 99) == 0
    assert observed["runs"] == tmp_path / "runs"


def test_clean_run_root_exits_before_default_worker_factory_starts_worker(
    tmp_path: Path, monkeypatch: Any
) -> None:
    task = tmp_path / "task.json"
    task.write_text('{"task_id":"task"}')
    cfg = config(tmp_path)
    called: list[list[str]] = []

    def flow(args: list[str], **kwargs: Any) -> tuple[Any, dict[str, bool]]:
        kwargs["runner"]("run", args)
        raise AssertionError("runner must reject clean-run-root before worker")

    monkeypatch.setattr(cli, "run_flow", flow)
    argv = [
        "run",
        "--planner-config",
        str(cfg),
        "--task",
        str(task),
        "--checkout",
        str(tmp_path / "checkout"),
        "--model",
        "m",
        "--context-cap",
        "1",
        "--canary-report",
        "c",
        "--clean-run-root",
    ]
    assert cli.main(argv, worker_main=lambda args: called.append(args) or 0) == 2
    assert called == []
