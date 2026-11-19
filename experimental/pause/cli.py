"""PAUSE's entry point (#476):

    python -m experimental.pause.cli run --task task.json --checkout <run_root>/checkout ...
    python -m experimental.pause.cli gate-replay <candidate_dir> --gate-config FILE

Without ``--planner-config`` (the flag off, the default) ``run`` passes the arguments unchanged to
``python -m experimental.local_worker.cli run``: the same episode, the same output, no cloud
request and no PAUSE receipt (AC1). With ``--planner-config``, ``main`` uses a configured or
injected factory to build the planner, gate, two-run runner and optional Core judge. A missing
Core section fails closed to R0. ``gate-replay`` only runs the gate over saved plans."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, cast

from experimental.local_worker import cli as worker_cli
from experimental.pause import receipt as receipt_module
from experimental.pause import replay as replay_module
from experimental.pause.client import CommandTransport, HaikuGate, HaikuPlanner
from experimental.pause.flow import CoreVerdict, Runner, RunResult, run_flow
from experimental.pause.interfaces import CallSettings, Gate, Planner
from experimental.pause.schema import RECEIPTS_FILE
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


def _client(config: dict, planner: bool) -> tuple[Planner | Gate, CallSettings]:
    settings = CallSettings(
        config["model_id"],
        config["effort"],
        float(config["timeout_seconds"]),
        int(config["max_attempts"]),
    )
    command = config["transport_command"]
    names = config.get("scan_names", [])
    if not isinstance(command, list) or not command or not all(type(c) is str for c in command):
        raise ValueError("transport_command must be a non-empty list of strings")
    if not isinstance(names, list) or not all(type(name) is str for name in names):
        raise ValueError("scan_names must be a list of strings")
    transport, scanner = CommandTransport(command), Scanner(tuple(names))
    return (
        HaikuPlanner(transport, scanner) if planner else HaikuGate(transport, scanner)
    ), settings


def _configured_run(
    config_path: str, worker_main: Callable[[list[str]], int] | None = None
) -> tuple[
    Planner,
    Gate,
    CallSettings,
    CallSettings,
    Runner,
    Callable[[RunResult], CoreVerdict] | None,
]:
    from experimental.pause.core_judge import CoreJudge
    from experimental.pause.runner import WorkerRunner

    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("run config must be an object")
    planner_value, ps = _client(config["planner"], True)
    gate_value, gs = _client(config["gate"], False)
    planner, gate = cast(Planner, planner_value), cast(Gate, gate_value)
    runs_override = config.get("runs_dir")
    if runs_override is not None and (not isinstance(runs_override, str) or not runs_override):
        raise ValueError("runs_dir must be a non-empty path when provided")
    judge: Callable[[RunResult], CoreVerdict] | None = None
    if "core" in config:
        core = config["core"]
        if not isinstance(core, dict) or not isinstance(core.get("state_dir"), str):
            raise ValueError("core.state_dir must be a path")
        verifiers = core.get("verifiers", [])
        if not isinstance(verifiers, list) or any(
            not isinstance(row, list)
            or len(row) != 2
            or type(row[0]) is not str
            or not isinstance(row[1], list)
            or not all(type(arg) is str for arg in row[1])
            for row in verifiers
        ):
            raise ValueError("core.verifiers must be [[id, [args...]], ...]")
        imports = core.get("import_paths", [])
        if not isinstance(imports, list) or not all(type(path) is str for path in imports):
            raise ValueError("core.import_paths must be a list of paths")
        judge = CoreJudge(
            Path(core["state_dir"]),
            cast(Sequence[tuple[str, Sequence[str]]], verifiers),
            imports,
        )
    return (
        planner,
        gate,
        ps,
        gs,
        WorkerRunner(
            worker_main or worker_cli.main,
            Path(runs_override) if runs_override is not None else None,
        ),
        judge,
    )


def _worker_options(runner: Runner, worker_args: list[str]) -> Any:
    """Parse worker options and reject settings that conflict with PAUSE's two-run flow."""
    parsed = worker_cli.parser().parse_known_args(["run", *worker_args])[0]
    if parsed.clean_run_root:
        raise ValueError("PAUSE cannot use --clean-run-root because it needs the R0 run root")
    target: Any = runner
    if hasattr(target, "runs_dir") and target.runs_dir is None:
        target.runs_dir = parsed.runs.expanduser()
    return parsed


def main(
    argv: list[str] | None = None,
    worker_main: Callable[[list[str]], int] | None = None,
    gate_factory: Callable[[str], tuple[Gate, CallSettings]] | None = None,
    run_factory: Callable[
        [str],
        tuple[
            Planner,
            Gate,
            CallSettings,
            CallSettings,
            Runner,
            Callable[[RunResult], CoreVerdict] | None,
        ],
    ]
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
            index = args.index(FLAG)
            config_path = args[index + 1]
            worker_args = args[1:index] + args[index + 2 :]
        except (ValueError, IndexError):
            pair = next((a.split("=", 1)[1] for a in args[1:] if a.startswith(FLAG + "=")), None)
            if pair is None:
                print(USAGE, file=sys.stderr)
                return 2
            config_path = pair
            worker_args = [a for a in args[1:] if not a.startswith(FLAG + "=")]
        try:
            planner, gate, ps, gs, runner, judge = (
                run_factory(config_path)
                if run_factory is not None
                else _configured_run(config_path, worker_main)
            )
            # Use the worker parser's own option semantics for task and output path.
            parsed = _worker_options(runner, worker_args)
            task_id = json.loads(parsed.task.read_text(encoding="utf-8"))["task_id"]
            out_arg = parsed.out
            receipt_path = Path(out_arg).expanduser().resolve() / RECEIPTS_FILE
            result, rec = run_flow(
                worker_args,
                task_id=task_id,
                planner=planner,
                gate=gate,
                planner_settings=ps,
                gate_settings=gs,
                runner=runner,
                core_verdict=judge,
                event_seq=lambda _: receipt_module.next_event_seq(receipt_path),
            )
            receipt_module.append(receipt_path, rec)
        except (OSError, ValueError, KeyError, IndexError, TypeError, SystemExit) as exc:
            print(f"pause: configured run failed closed: {exc}", file=sys.stderr)
            return 2
        print(result.output, end="")
        print(json.dumps(rec, separators=(",", ":")))
        return result.code
    return (worker_main or worker_cli.main)(args)


if __name__ == "__main__":
    sys.exit(main())
