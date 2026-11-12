"""PAUSE's entry point (#476):

    python -m experimental.pause.cli run --task task.json --checkout <run_root>/checkout ...
    python -m experimental.pause.cli gate-replay <candidate_dir> --gate-config FILE

Without ``--planner-config`` (the flag off, the default) ``run`` passes the arguments unchanged to
``python -m experimental.local_worker.cli run`` (AC1). Configured execution is supplied by an
injectable factory so callers can bind a checkout guard and event log before worker tool calls."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path

from experimental.local_worker import cli as worker_cli
from experimental.pause import replay as replay_module
from experimental.pause.client import CommandTransport, HaikuGate, HaikuPlanner
from experimental.pause.interfaces import CallSettings, Gate, Planner
from experimental.pause.secrets_scan import Scanner

FLAG = "--planner-config"
USAGE = (
    "usage: python -m experimental.pause.cli run [--planner-config FILE] <local_worker run args>\n"
    "       python -m experimental.pause.cli gate-replay <candidate_dir> --gate-config FILE"
)


def _settings(config: dict, key: str) -> CallSettings:
    row = config[key]
    return CallSettings(
        row["model_id"], row["effort"], float(row["timeout_seconds"]), int(row["max_attempts"])
    )


def _configured_clients(path: str) -> tuple[Planner, Gate, CallSettings, CallSettings]:
    config = json.loads(Path(path).read_text(encoding="utf-8"))
    command = config["transport_command"]
    if not isinstance(command, list) or not command or not all(isinstance(c, str) for c in command):
        raise ValueError("transport_command must be a non-empty list of strings")
    names = config.get("scan_names", [])
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        raise ValueError("scan_names must be a list of strings")
    transport, scanner = CommandTransport(command), Scanner(tuple(names))
    return (
        HaikuPlanner(transport, scanner),
        HaikuGate(transport, scanner),
        _settings(config, "planner"),
        _settings(config, "gate"),
    )


def _configured_gate(path: str) -> tuple[Gate, CallSettings]:
    config = json.loads(Path(path).read_text(encoding="utf-8"))
    command = config["transport_command"]
    names = config.get("scan_names", [])
    if not isinstance(command, list) or not command or not all(isinstance(c, str) for c in command):
        raise ValueError("transport_command must be a non-empty list of strings")
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        raise ValueError("scan_names must be a list of strings")
    settings = CallSettings(
        config["model_id"],
        config["effort"],
        float(config["timeout_seconds"]),
        int(config["max_attempts"]),
    )
    return HaikuGate(CommandTransport(command), Scanner(tuple(names))), settings


def main(
    argv: list[str] | None = None,
    worker_main: Callable[[list[str]], int] | None = None,
    gate_factory: Callable[[str], tuple[Gate, CallSettings]] | None = None,
    run_factory: Callable[[list[str], Planner, Gate, CallSettings, CallSettings], int]
    | None = None,
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
        try:
            index = next(
                i for i, value in enumerate(args) if value == FLAG or value.startswith(FLAG + "=")
            )
            token = args[index]
            if token.startswith(FLAG + "="):
                config_path = token.partition("=")[2]
                remainder = args[:index] + args[index + 1 :]
            else:
                config_path = args[index + 1]
                remainder = args[:index] + args[index + 2 :]
            if not config_path or config_path.startswith("--"):
                raise ValueError("--planner-config requires FILE")
            if run_factory is None:
                raise ValueError("--planner-config has no configured checkout/event controller")
            planner, gate, planner_settings, gate_settings = _configured_clients(config_path)
            return run_factory(remainder, planner, gate, planner_settings, gate_settings)
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            print(f"pause: --planner-config configuration or run error: {exc}", file=sys.stderr)
            return 2
        except IndexError:
            print("pause: --planner-config requires FILE", file=sys.stderr)
            return 2
    return (worker_main or worker_cli.main)(args)


if __name__ == "__main__":
    sys.exit(main())
