"""Task file, canary copy and argv for one local-worker episode."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from experimental.local_relay.hidden_tests import visible_targets


def write_task_file(run_root: Path, task: dict) -> Path:
    task_path = run_root / "task.json"
    task_path.write_text(
        json.dumps(
            {
                "task_id": task["task_id"],
                "repo": "local-relay",
                "prompt": task["prompt"],
                "public_tests": list(
                    visible_targets(
                        tuple(t for _name, command in task["verifiers"] for t in command[4:]),
                        task.get("hidden"),
                    )
                ),
            }
        ),
        encoding="utf-8",
    )
    return task_path


def stage_canary(canary: Path, out: Path) -> Path:
    if not canary.is_file():
        raise ValueError(f"canary report does not exist: {canary}")
    local_report = out / "canary.json"
    shutil.copyfile(canary, local_report)
    shutil.copyfile(Path(str(canary) + ".sha256"), Path(str(local_report) + ".sha256"))
    return local_report


def worker_argv(
    task, parts, model, context, endpoint, max_steps: int = 200, max_wall_seconds: float = 960.0
) -> list[str]:
    task_path, checkout, out, local_report, runs = parts
    return [
        sys.executable,
        "-m",
        "experimental.local_worker.cli",
        "run",
        "--task",
        str(task_path),
        "--checkout",
        str(checkout),
        "--out",
        str(out),
        "--model",
        model,
        "--context-cap",
        str(context),
        "--canary-report",
        str(local_report),
        "--runs",
        str(runs),
        *[value for path in task["allowed_files"] for value in ("--write-path", path)],
        "--endpoint",
        endpoint,
        "--max-steps",
        str(max_steps),
        "--max-wall-seconds",
        str(max_wall_seconds),
    ]
