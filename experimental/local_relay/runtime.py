"""Default runtime assembly for one local relay task."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import uuid
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from cairnwork.intake import Intake
from cairnwork.issue_github import GitHub
from cairnwork.issue_runner import DEFAULT_GH, QUEUE, _instructions
from cairnwork.issue_task import _check_path, parse_task_block, pytest_argv
from cairnwork.taskpaths import check_task_paths
from cairnwork.verify_candidate import verify_candidate
from experimental.local_relay.adapters import (
    CoreAcceptance,
    HaikuProxyFallback,
    LocalWorkerAdapter,
)
from experimental.local_relay.config import (
    DEFAULT_CONTEXT_TOKENS,
    DEFAULT_HAIKU_PROXY_URL,
    DEFAULT_LEDGER_PATH,
    DEFAULT_LOCAL_ENDPOINT,
    HAIKU_MODEL,
    RETRY_CONTEXT_TOKENS,
    RelayConfig,
)
from experimental.local_relay.diagnostics import append_debug, redact
from experimental.local_relay.evidence import RecordingVerify
from experimental.local_relay.gate_plan import build_gate_plan
from experimental.local_relay.hidden_tests import (
    HiddenVerify,
    load_hidden,
    require_hidden_targets,
    visible_targets,
    with_python,
)
from experimental.local_relay.process import spawn_worker as _spawn_worker
from experimental.local_relay.proxy_gate import ProxyGate
from experimental.local_relay.relay import run_relay
from experimental.local_relay.thinking_shim import thinking_off
from experimental.local_relay.worker_launch import stage_canary, worker_argv, write_task_file
from experimental.local_relay.worker_setup import (
    executable_paths,
    prepare_worker_layout,
    restore_judge_modes,
)
from experimental.local_worker import corpus_io
from experimental.local_worker.endpoint import parse_loopback_url
from experimental.local_worker.run_root_cleanup import INSTALLED_RUNS
from experimental.pause.core_judge import CoreJudge
from experimental.pause.flow import RunResult
from experimental.pause.runner import _prepare

FIXTURE_TASK = "cairnwork-local-relay.json"
LOCAL_MODEL = "qwen3.5-9b"


def dry_route(args: Any) -> dict[str, Any]:
    """Describe routing without creating clients, opening paths or contacting services."""
    proxy_url = str(parse_loopback_url(getattr(args, "haiku_proxy_url", DEFAULT_HAIKU_PROXY_URL)))
    return {
        "route": "local-relay",
        "endpoint": DEFAULT_LOCAL_ENDPOINT,
        "model": LOCAL_MODEL,
        "context_tokens": f"{DEFAULT_CONTEXT_TOKENS} -> {RETRY_CONTEXT_TOKENS}",
        "gate": {
            "endpoint": proxy_url,
            "model": HAIKU_MODEL,
            "tools": False,
            "label": "<task>-gate-<stage>",
        },
        "fallback": {"endpoint": proxy_url, "model": HAIKU_MODEL, "context_tokens": 65536},
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
    hidden = task.get("hidden")
    debug = _debug_path(args, task["task_id"])
    recorder = RecordingVerify(
        HiddenVerify(task["repo"], hidden) if hidden else verify_candidate,
        (lambda tail: append_debug(debug, {"event": "core-log-tail", "tail": tail}))
        if hidden
        else None,
    )
    judge = CoreJudge(state, task["verifiers"], config["import_paths"], verify=recorder)
    if hidden:
        judge.intake = with_python(judge.intake, hidden.python)
    acceptance = CoreAcceptance(
        judge, task["allowed_files"], task["repo"], detail=lambda: recorder.last
    )
    gate = ProxyGate(
        str(parse_loopback_url(getattr(args, "haiku_proxy_url", DEFAULT_HAIKU_PROXY_URL)))
    )
    fallback_url = str(
        parse_loopback_url(getattr(args, "haiku_proxy_url", DEFAULT_HAIKU_PROXY_URL))
    )

    def launch(context, endpoint):
        if endpoint == fallback_url:  # the Haiku proxy needs no thinking switch
            return _run_localworker(
                args, task, task["repo"], context, endpoint, canary, HAIKU_MODEL
            )
        with thinking_off(endpoint) as shim_url:  # 9B: thinking off, as in the experiment
            return _run_localworker(args, task, task["repo"], context, shim_url, canary)

    worker = LocalWorkerAdapter(launch, model_id=LOCAL_MODEL)
    fallback = HaikuProxyFallback(
        fallback_url,
        lambda context, endpoint: worker.run_with_endpoint(context, endpoint),
    )

    def run_once(_args: Any, ledger: Any) -> dict[str, Any]:
        outcome = run_relay(
            task["task_id"],
            worker,
            acceptance,
            gate,
            fallback,
            ledger,
            replace(RelayConfig.from_env(), plan_for=lambda cand: build_gate_plan(task, cand)),
        )
        return {
            "route": outcome.route,
            "task_id": task["task_id"],
            "acceptance_passed": outcome.acceptance_passed,
            "acceptance_raw": outcome.acceptance_raw,
            "gate_verdict": outcome.gate_verdict,
            "reason": outcome.reason,
            "failure": outcome.reason if outcome.route in {"FAILED", "LEDGER_ERROR"} else None,
            "stages": list(outcome.stages),
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
    hidden = load_hidden(data, root)
    targets = tuple(target for _name, command in verifiers for target in command[4:])
    if hidden:
        require_hidden_targets(targets, hidden)
    check_task_paths(root, base, tuple(data["allowed_files"]), visible_targets(targets, hidden))
    return {
        "task_id": str(data["task_id"]),
        "prompt": str(data["prompt"]),
        "title": str(data.get("title", data["task_id"])),
        "allowed_files": data["allowed_files"],
        "verifiers": verifiers,
        "base_commit": base,
        "repo": root,
        "hidden": hidden,
    }


def _run_localworker(args, task, source, context, endpoint, canary, model=LOCAL_MODEL):
    if canary is None:
        raise ValueError("--canary-report is required for the real local worker")
    runs = _local_runs(args)
    run_root = runs / uuid.uuid4().hex
    checkout = run_root / "checkout"
    run_root.mkdir(mode=0o750)
    os.chmod(run_root, 0o750)
    _prepare(task["repo"], checkout, task["base_commit"])
    hidden = task.get("hidden")
    prepare_worker_layout(run_root, checkout, hidden.python if hidden else None)
    base = _git(checkout, "rev-parse", "HEAD")
    executable = executable_paths(Path(task["repo"]), base)
    task_path = write_task_file(run_root, task)
    out = run_root / "episodes"
    out.mkdir(mode=0o700)
    local_report = stage_canary(canary, out)
    limits = RelayConfig.from_env()
    argv = worker_argv(
        task,
        (task_path, checkout, out, local_report, runs),
        model,
        context,
        endpoint,
        limits.max_steps,
        limits.max_wall_seconds,
    )
    # Never run from the task repo: `-m` would put it first on sys.path (module shadowing).
    cwd = Path(__file__).resolve().parents[2]
    debug_path = _debug_path(args, task["task_id"])
    append_debug(
        debug_path,
        {
            "event": "worker-start",
            "argv": argv,
            "cwd": str(cwd),
            "run_root": str(run_root),
            "checkout": str(checkout),
            "run_root_stat": _path_identity(run_root),
            "checkout_stat": _path_identity(checkout),
        },
    )
    result = _spawn_worker(argv, cwd)
    record = _episode_record(
        result.stdout, corpus_io.episodes_path(out, "cairnwork", model, "pilot")
    )
    details = _worker_diagnostics(result.returncode, result.stdout, result.stderr, record)
    completed = _episode_completed(result.returncode, record)
    if isinstance(record, dict) and not completed:
        record = {**record, "_local_relay_diagnostics": details}
    append_debug(debug_path, {"event": "worker-finish", **details})
    restore_judge_modes(checkout, executable)
    return RunResult(
        result.returncode,
        result.stdout + ("" if completed else redact(result.stderr)),
        run_root,
        checkout,
        record=record,
        base_sha=base,
    )


def _episode_completed(returncode: int, record: Any) -> bool:
    if returncode != 0 or not isinstance(record, dict):
        return False
    verification = record.get("verification")
    feedback = record.get("spec_feedback")
    return (
        isinstance(verification, dict)
        and verification.get("exit_reason") == "COMPLETED"
        and isinstance(feedback, dict)
        and feedback.get("needs_spec") is False
    )


def _local_runs(args: Any) -> Path:
    runs = Path(getattr(args, "runs", INSTALLED_RUNS)).expanduser().resolve()
    if not runs.is_dir():
        raise ValueError(f"local worker runs directory does not exist: {runs}")
    return runs


def _episode_record(output: str, episode_file: Path) -> dict[str, Any] | None:
    if not any(line.startswith("LOCAL_WORKER_EPISODE ") for line in output.splitlines()):
        return None
    try:
        rows = episode_file.read_text(encoding="utf-8").splitlines()
        record = json.loads(rows[-1]) if rows else None
    except (OSError, ValueError, IndexError):
        return None
    return record if isinstance(record, dict) else None


def _worker_diagnostics(returncode: int, stdout: str, stderr: str, record) -> dict[str, Any]:
    verification = record.get("verification", {}) if isinstance(record, dict) else {}
    termination = record.get("termination", {}) if isinstance(record, dict) else {}
    marker = next(
        (line for line in stdout.splitlines() if line.startswith("LOCAL_WORKER_EPISODE ")), ""
    )
    refused = next(
        (line for line in stderr.splitlines() if line.startswith("LOCAL_WORKER_EPISODE_REFUSED ")),
        "",
    )
    status = verification.get("exit_reason") if isinstance(verification, dict) else None
    if status is None:
        status = (
            "REFUSED"
            if refused
            else marker.split()[1]
            if len(marker.split()) > 1
            else "NO_EPISODE_RECORD"
        )
    stop = (
        (termination.get("abort_cause") or termination.get("limit_hit"))
        if isinstance(termination, dict)
        else None
    )
    if stop is None and refused:
        stop = refused.removeprefix("LOCAL_WORKER_EPISODE_REFUSED ")
    return {
        "process_exit_code": returncode,
        "episode_status": status,
        "stop_reason": stop,
        "stderr": redact(stderr),
    }


def _debug_path(args: Any, task_id: str) -> Path | None:
    ledger = getattr(args, "ledger", None)
    if not ledger:
        return None
    safe_id = "".join(char if char.isalnum() or char in "_.-" else "_" for char in task_id)
    return Path(ledger).expanduser().resolve().parent / f"relay-debug-{safe_id}.log"


def _path_identity(path: Path) -> dict[str, Any]:
    info = path.stat()
    return {"uid": info.st_uid, "gid": info.st_gid, "mode": f"{info.st_mode & 0o7777:04o}"}


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=False
    )
    if result.returncode:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()
