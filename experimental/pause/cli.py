"""PAUSE's entry point (#476):

    python -m experimental.pause.cli run --task task.json --checkout <run_root>/checkout ...
    python -m experimental.pause.cli gate-replay <candidate_dir> --gate-config FILE

Without ``--planner-config`` (the flag off, the default) ``run`` passes the arguments unchanged to
``python -m experimental.local_worker.cli run``: the same episode, the same output, no cloud
request and no PAUSE receipt (AC1). ``--planner-config`` is refused (exit 2, before the worker
runs; a CLI message, not a schema.Reason) until step 3 wires the planner, the gate and the
resume. ``gate-replay`` (step 2) only runs the gate over saved plans: no worker, no run
directory, nothing written."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path

from experimental.local_worker import cli as worker_cli
from experimental.pause import replay as replay_module
from experimental.pause.client import CommandTransport, HaikuGate
from experimental.pause.interfaces import CallSettings, Gate
from experimental.pause.secrets_scan import Scanner

FLAG = "--planner-config"
USAGE = (
    "usage: python -m experimental.pause.cli run [--planner-config FILE] <local_worker run args>\n"
    "       python -m experimental.pause.cli gate-replay <candidate_dir> --gate-config FILE"
)


def _configured_gate(path: str) -> tuple[Gate, CallSettings]:
    config = json.loads(Path(path).read_text(encoding="utf-8"))
    settings = CallSettings(
        config["model_id"],
        config["effort"],
        float(config["timeout_seconds"]),
        int(config["max_attempts"]),
    )
    command = config["transport_command"]
    if not isinstance(command, list) or not command or not all(isinstance(c, str) for c in command):
        raise ValueError("transport_command must be a non-empty list of strings")
    names = config.get("scan_names", [])
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        raise ValueError("scan_names must be a list of strings")
    return HaikuGate(CommandTransport(command), Scanner(tuple(names))), settings


def main(
    argv: list[str] | None = None,
    worker_main: Callable[[list[str]], int] | None = None,
    gate_factory: Callable[[str], tuple[Gate, CallSettings]] | None = None,
) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["gate-replay"]:
        if len(args) != 4 or args[2] != "--gate-config":
            print(USAGE, file=sys.stderr)
            return 2
        try:
            gate, settings = (gate_factory or _configured_gate)(args[3])
            report = replay_module.replay(args[1], gate, settings)
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            print(f"pause: gate-replay configuration or input error: {exc}", file=sys.stderr)
            return 2
        print(
            json.dumps(
                {"results": [r.__dict__ for r in report.results], "agreement": report.agreement}
            )
        )
        return 0
    if args[:1] != ["run"]:
        print(USAGE, file=sys.stderr)
        return 2
    if any(a == FLAG or a.startswith(FLAG + "=") for a in args[1:]):
        print("pause: --planner-config is not wired yet (#476 steps 2-3)", file=sys.stderr)
        return 2
    return (worker_main or worker_cli.main)(args)


if __name__ == "__main__":
    sys.exit(main())
