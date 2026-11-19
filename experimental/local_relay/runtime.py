"""Default runtime assembly for one local relay task."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import uuid
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from cairnwork.intake import Intake
from cairnwork.issue_github import GitHub
from cairnwork.issue_runner import DEFAULT_GH, QUEUE, _instructions
from cairnwork.issue_task import _check_path, parse_task_block, pytest_argv
from cairnwork.taskpaths import check_task_paths
from cairnwork.workers.agy import AgyWorker
from cairnwork.workers.agy_dispatch import Dispatch
from experimental.local_relay.adapters import (
    AgyFallback,
    AgyQuotaLedger,
    CoreAcceptance,
    CostGate,
    LocalWorkerAdapter,
)
from experimental.local_relay.config import (
    DEFAULT_CONTEXT_TOKENS,
    DEFAULT_LEDGER_PATH,
    DEFAULT_LOCAL_ENDPOINT,
    RETRY_CONTEXT_TOKENS,
    RelayConfig,
)
from experimental.local_relay.relay import run_relay
from experimental.local_worker import corpus_io
from experimental.local_worker.endpoint import EndpointUnreachable
from experimental.pause.core_judge import CoreJudge
from experimental.pause.flow import RunResult

FIXTURE_TASK = "cairnwork-local-relay.json"
LOCAL_MODEL = "qwen3.5-9b"


def dry_route(args: Any) -> dict[str, Any]:
    """Describe routing without creating clients, opening paths or contacting services."""
    return {
        "route": "local-relay",
        "endpoint": DEFAULT_LOCAL_ENDPOINT,
        "model": LOCAL_MODEL,
        "context_tokens": f"{DEFAULT_CONTEXT_TOKENS} -> {RETRY_CONTEXT_TOKENS}",
        "gate_transport": [
            str(Path.home() / ".local/bin/claude-api-run"),
            "--tag",
            "cw-local-relay",
        ],
        "fallback": "AgyWorker",
        "ledger": str(Path(args.ledger).expanduser()) if args.ledger else DEFAULT_LEDGER_PATH,
        "enabled": os.environ.get("CAIRNWORK_LOCAL_RELAY") == "1",
        "flags": {
            "CAIRNWORK_LOCAL_RELAY": os.environ.get("CAIRNWORK_LOCAL_RELAY"),
            "dry_route": bool(args.dry_route),
            "fixture": bool(args.fixture),
        },
        "fixture": str(args.fixture.resolve()) if args.fixture else None,
    }


def build_run_once(args: Any) -> Callable[[Any, Any], dict[str, Any]]:
    """Bind a task source and return the ``cli.main`` run_once callback."""
    task = _fixture_task(args.fixture) if args.fixture else _issue_task(args)
    state = Path(args.state).resolve()
    canary = Path(args.canary_report).expanduser().resolve() if args.canary_report else None
    intake = Intake(state)
    config = intake.config()
    account = intake.verifier_account()
    judge = CoreJudge(state, task["verifiers"], config["import_paths"])
    acceptance = CoreAcceptance(judge, task["allowed_files"])
    gate = CostGate()
    worker = LocalWorkerAdapter(
        lambda context, endpoint: _run_localworker(
            args, task, task["repo"], context, endpoint, canary
        ),
        model_id=LOCAL_MODEL,
    )
    agy = AgyWorker(
        model=config["model"],
        agy=config["agy"],
        dispatch=_agy_dispatch(config),
        verifier_user=getattr(account, "user", None),
    )
    fallback = AgyFallback(
        agy,
        lambda selected, _task_id: _run_agy(selected, task, task["repo"], state),
        quota_units=lambda result: (
            int(result.get("quota_units", 0))
            if isinstance(result, dict) and type(result.get("quota_units", 0)) is int
            else 0
        ),
    )

    def run_once(_args: Any, ledger: Any) -> dict[str, Any]:
        outcome = run_relay(
            task["task_id"],
            worker,
            acceptance,
            gate,
            fallback,
            AgyQuotaLedger(ledger, fallback),
            RelayConfig.from_env(),
        )
        return {
            "route": outcome.route,
            "task_id": task["task_id"],
            "acceptance_passed": outcome.acceptance_passed,
            "acceptance_raw": outcome.acceptance_raw,
            "gate_verdict": outcome.gate_verdict,
            "reason": outcome.reason,
        }

    return run_once


def _fixture_task(repo: Path | None) -> dict[str, Any]:
    if repo is None:
        raise ValueError("fixture repo is required")
    root = repo.resolve()
    data = json.loads((root / FIXTURE_TASK).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{FIXTURE_TASK} must contain a JSON object")
    return _validate_task(data, root)


def _issue_task(args: Any) -> dict[str, Any]:
    github = GitHub(args.gh or DEFAULT_GH, args.repo)
    for issue in github.issues(QUEUE):
        number = int(issue["number"])
        if github.trust(number, QUEUE):
            continue
        parsed = parse_task_block(str(issue.get("body") or ""), None)
        base = _git(Path(args.clone), "rev-parse", "origin/main")
        return {
            "task_id": f"issue-{number}",
            "prompt": _instructions(args.repo, number, issue, parsed.summary),
            "title": str(issue.get("title") or f"Issue #{number}"),
            "allowed_files": list(parsed.allowed_files),
            "verifiers": [(f"pytest-{i}", command) for i, command in enumerate(parsed.verifiers)],
            "base_commit": base,
            "repo": Path(args.clone).resolve(),
        }
    raise ValueError("no trusted queued Cairnwork Issue is available")


def _validate_task(data: dict[str, Any], root: Path) -> dict[str, Any]:
    required = ("task_id", "prompt", "allowed_files", "verifiers")
    if any(not data.get(key) for key in required):
        raise ValueError(f"{FIXTURE_TASK} requires {', '.join(required)}")
    if not isinstance(data["task_id"], str) or not corpus_io.ID_RE.fullmatch(data["task_id"]):
        raise ValueError("fixture task_id must be a local worker id")
    if not isinstance(data["prompt"], str) or not data["prompt"].strip():
        raise ValueError("fixture prompt must be non-empty text")
    if not isinstance(data["allowed_files"], list) or not all(
        isinstance(item, str) for item in data["allowed_files"]
    ):
        raise ValueError("fixture allowed_files must be a list of paths")
    if not 0 < len(data["allowed_files"]) <= 10:
        raise ValueError("fixture allowed_files must contain 1 to 10 paths")
    if len(set(data["allowed_files"])) != len(data["allowed_files"]):
        raise ValueError("fixture allowed_files must not contain duplicates")
    for path in data["allowed_files"]:
        _check_path(path)
    if not isinstance(data["verifiers"], list) or not all(
        isinstance(item, list) and all(isinstance(part, str) for part in item)
        for item in data["verifiers"]
    ):
        raise ValueError("fixture verifiers must be a list of argv lists")
    verifiers = [
        (f"pytest-{i}", pytest_argv(shlex.join(command), None))
        for i, command in enumerate(data["verifiers"])
    ]
    if not verifiers:
        raise ValueError("fixture must include at least one verifier")
    base = _git(root, "rev-parse", "HEAD")
    check_task_paths(
        root,
        base,
        tuple(data["allowed_files"]),
        tuple(target for _name, command in verifiers for target in command[4:]),
    )
    return {
        "task_id": str(data["task_id"]),
        "prompt": str(data["prompt"]),
        "title": str(data.get("title", data["task_id"])),
        "allowed_files": data["allowed_files"],
        "verifiers": verifiers,
        "base_commit": base,
        "repo": root,
    }


def _run_localworker(args, task, source, context, endpoint, canary):
    if canary is None:
        raise ValueError("--canary-report is required for the real local worker")
    run_root = Path(args.state).resolve() / "local-relay-runs" / uuid.uuid4().hex
    checkout = run_root / "checkout"
    run_root.mkdir(parents=True, mode=0o700)
    _git(source, "clone", "--no-hardlinks", str(task["repo"]), str(checkout))
    _git(checkout, "checkout", "--detach", task["base_commit"])
    base = _git(checkout, "rev-parse", "HEAD")
    task_path = run_root / "task.json"
    task_path.write_text(
        json.dumps(
            {
                "task_id": task["task_id"],
                "repo": "local-relay",
                "prompt": task["prompt"],
                "public_tests": [
                    target for _name, command in task["verifiers"] for target in command[4:]
                ],
            }
        ),
        encoding="utf-8",
    )
    out = run_root / "episodes"
    out.mkdir(mode=0o700)
    report = canary
    if not report.is_file():
        raise ValueError(f"canary report does not exist: {report}")
    local_report = out / "canary.json"
    shutil.copyfile(report, local_report)
    shutil.copyfile(Path(str(report) + ".sha256"), Path(str(local_report) + ".sha256"))
    result = subprocess.run(
        [
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
            LOCAL_MODEL,
            "--context-cap",
            str(context),
            "--canary-report",
            str(local_report),
            *[value for path in task["allowed_files"] for value in ("--write-path", path)],
            "--endpoint",
            endpoint,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == EndpointUnreachable.exit_code:
        raise EndpointUnreachable("local worker endpoint is unreachable")
    record = _episode_record(
        result.stdout, corpus_io.episodes_path(out, "cairnwork", LOCAL_MODEL, "pilot")
    )
    complete = (
        result.returncode == 0
        and isinstance(record, dict)
        and record.get("verification", {}).get("exit_reason") == "COMPLETED"
        and not record.get("spec_feedback", {}).get("needs_spec", True)
    )
    return RunResult(
        0 if complete else 1,
        result.stdout + result.stderr,
        run_root,
        checkout,
        record=record,
        base_sha=base,
    )


def _episode_record(output: str, episode_file: Path) -> dict[str, Any] | None:
    if not any(line.startswith("LOCAL_WORKER_EPISODE COMPLETED ") for line in output.splitlines()):
        return None
    try:
        rows = episode_file.read_text(encoding="utf-8").splitlines()
        record = json.loads(rows[-1]) if rows else None
    except (OSError, ValueError, IndexError):
        return None
    return record if isinstance(record, dict) else None


def _run_agy(worker: AgyWorker, task: dict[str, Any], source: Path, state: Path) -> RunResult:
    root = state / "local-relay-runs" / uuid.uuid4().hex
    checkout = root / "checkout"
    root.mkdir(parents=True, mode=0o700)
    _git(source, "clone", "--no-hardlinks", str(task["repo"]), str(checkout))
    _git(checkout, "checkout", "--detach", task["base_commit"])
    base = _git(checkout, "rev-parse", "HEAD")
    agy_task = SimpleNamespace(
        title=task["title"],
        instructions=task["prompt"],
        allowed_files=tuple(task["allowed_files"]),
        test_commit="",
    )
    details = worker.run(checkout, agy_task)
    return RunResult(0, json.dumps(details), root, checkout, base_sha=base)


def _agy_dispatch(config: dict[str, Any]) -> Dispatch | None:
    path = config["agy_dispatch"]
    return (
        None
        if path is None
        else Dispatch(
            path,
            config["agy_dispatch_pool_wait_seconds"],
            config["agy_dispatch_provider_stall_seconds"],
            config["agy_dispatch_stream_no_progress_seconds"],
            run_as_user=config["agy_run_as_user"],
        )
    )


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=False
    )
    if result.returncode:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()
