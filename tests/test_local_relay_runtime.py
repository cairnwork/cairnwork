"""Default assembly and CLI diagnostics stay offline under fake dependencies."""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

from cairnwork.issue_task import IssueRefused
from experimental.local_relay import cli, runtime
from experimental.local_relay.config import (
    DEFAULT_HAIKU_PROXY_URL,
    DEFAULT_LOCAL_ENDPOINT,
    HAIKU_MODEL,
)
from experimental.pause.flow import RunResult


def _args(tmp_path: Path, *extra: str) -> list[str]:
    return [
        "--state",
        str(tmp_path / "state"),
        "--repo",
        "owner/repo",
        "--clone",
        str(tmp_path / "clone"),
        *extra,
        "run-once",
    ]


def test_dry_route_prints_real_configuration_without_building_runtime(
    monkeypatch, capsys, tmp_path
):
    monkeypatch.delenv("CAIRNWORK_LOCAL_RELAY", raising=False)
    monkeypatch.setattr(cli, "build_run_once", lambda *_: pytest.fail("runtime built"))
    code = cli.main(_args(tmp_path, "--dry-route", "--ledger", str(tmp_path / "ledger.jsonl")))
    result = json.loads(capsys.readouterr().out)
    assert code == 0
    assert result["endpoint"] == DEFAULT_LOCAL_ENDPOINT
    assert result["context_tokens"] == "32768 -> 65536"
    assert result["gate"]["tools"] is False and result["gate"]["model"] == "claude-haiku-5-5"
    assert result["fallback"] == {
        "endpoint": DEFAULT_HAIKU_PROXY_URL,
        "model": HAIKU_MODEL,
        "context_tokens": 65536,
    }
    assert result["ledger"] == str(tmp_path / "ledger.jsonl")
    assert result["enabled"] is False
    assert not (tmp_path / "ledger.jsonl").exists()


def test_flag_off_refuses_before_runtime_assembly(monkeypatch, capsys, tmp_path):
    monkeypatch.delenv("CAIRNWORK_LOCAL_RELAY", raising=False)
    monkeypatch.setattr(cli, "build_run_once", lambda *_: pytest.fail("runtime built"))
    assert cli.main(_args(tmp_path)) == 2
    assert json.loads(capsys.readouterr().out)["error"] == "RELAY_DISABLED"


def test_flag_on_without_injection_builds_default_runtime(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("CAIRNWORK_LOCAL_RELAY", "1")
    assembled = []
    monkeypatch.setattr(
        cli,
        "build_run_once",
        lambda _args: assembled.append(True) or (lambda _a, _ledger: {"route": "LOCAL_FIRST"}),
    )
    assert cli.main(_args(tmp_path)) == 0
    assert assembled == [True]
    assert json.loads(capsys.readouterr().out)["route"] == "LOCAL_FIRST"


def _patch_runtime_assembly(monkeypatch, captured):
    class FakeIntake:
        def __init__(self, state):
            captured["state"] = state

        def config(self):
            return {
                "import_paths": [],
            }

        def verifier_account(self):
            return None

    class FakeJudge:
        def __init__(self, state, verifiers, import_paths, **_kw):
            captured["judge"] = (state, verifiers, import_paths)

    class FakeLocalWorker:
        def __init__(self, run_episode, **kwargs):
            captured["local"] = (run_episode, kwargs)

        def run_with_endpoint(self, context, endpoint):
            return captured["local"][0](context, endpoint)

    class FakeFallback:
        def __init__(self, endpoint, run_attempt, **kwargs):
            captured["fallback"] = (endpoint, run_attempt, kwargs)

    monkeypatch.setattr(runtime, "Intake", FakeIntake)
    monkeypatch.setattr(runtime, "CoreJudge", FakeJudge)
    monkeypatch.setattr(runtime, "LocalWorkerAdapter", FakeLocalWorker)
    monkeypatch.setattr(
        runtime, "ProxyGate", lambda url: captured.setdefault("gate_url", url) or object()
    )
    monkeypatch.setattr(runtime, "HaikuProxyFallback", FakeFallback)
    monkeypatch.setattr(runtime, "check_task_paths", lambda *_args: None)


def test_build_uses_tagged_transport_and_same_state_verifier(monkeypatch, tmp_path):
    captured = {}
    _patch_runtime_assembly(monkeypatch, captured)
    fixture = tmp_path / "repo"
    fixture.mkdir()
    (fixture / runtime.FIXTURE_TASK).write_text(
        json.dumps(
            {
                "task_id": "unit-task",
                "prompt": "fix",
                "allowed_files": ["a.py"],
                "verifiers": [["python3", "-m", "pytest", "tests/test_a.py"]],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(runtime, "_git", lambda *_args: "a" * 40)
    monkeypatch.setattr(
        runtime,
        "_run_localworker",
        lambda *values: (
            captured.__setitem__("episode", values),
            RunResult(0, "ok", tmp_path, fixture),
        )[1],
    )
    args = type(
        "Args",
        (),
        {
            "fixture": fixture,
            "state": str(tmp_path / "state"),
            "clone": str(fixture),
            "canary_report": None,
            "gh": "gh",
            "repo": "owner/repo",
            "haiku_proxy_url": "http://127.0.0.1:18399/v1",
        },
    )()

    runtime.build_run_once(args)
    assert captured["judge"][0] == tmp_path / "state"
    assert captured["judge"][1] == [
        ("pytest-0", ("python3", "-m", "pytest", "-q", "tests/test_a.py"))
    ]
    assert captured["gate_url"] == "http://127.0.0.1:18399/v1"  # same Haiku proxy, no tools
    assert captured["local"][1]["model_id"] == "qwen3.5-9b"
    assert captured["fallback"][0] == "http://127.0.0.1:18399/v1"
    captured["fallback"][1](65536, "http://127.0.0.1:18399/v1")
    assert captured["episode"][3:7] == (
        65536,
        "http://127.0.0.1:18399/v1",
        None,
        HAIKU_MODEL,
    )


def test_non_loopback_proxy_url_is_refused(tmp_path):
    code = cli.main(
        _args(tmp_path, "--dry-route", "--haiku-proxy-url", "http://example.com:18301/v1")
    )
    assert code != 0


def test_local_checkout_uses_worker_runner_prepare_contract(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    subprocess.run(["git", "-C", str(source), "config", "user.name", "Test"], check=True)
    subprocess.run(
        ["git", "-C", str(source), "config", "user.email", "test@example.invalid"], check=True
    )
    (source / "tracked.txt").write_text("clean\n", encoding="utf-8")
    (source / "tracked.txt").chmod(0o640)
    subprocess.run(["git", "-C", str(source), "add", "tracked.txt"], check=True)
    subprocess.run(["git", "-C", str(source), "commit", "-qm", "base"], check=True)
    base = subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "HEAD"], text=True
    ).strip()
    root = tmp_path / "runs" / "run"
    root.mkdir(parents=True, mode=0o750)
    os.chmod(root, 0o750)
    checkout = root / "checkout"

    runtime._prepare(source, checkout, base)

    status = subprocess.check_output(
        ["git", "-C", str(checkout), "status", "--porcelain"], text=True
    )
    assert status == ""
    assert stat.S_IMODE(checkout.stat().st_mode) == stat.S_IMODE(source.stat().st_mode)
    assert stat.S_IMODE((checkout / "tracked.txt").stat().st_mode) == 0o640
    assert checkout.stat().st_uid == source.stat().st_uid
    assert checkout.stat().st_gid == source.stat().st_gid


def test_invalid_fixture_is_rejected_without_services_or_home_writes(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / runtime.FIXTURE_TASK).write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="requires"):
        runtime._fixture_task(repo)


def test_fixture_refuses_path_escape_before_git_or_worker(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / runtime.FIXTURE_TASK).write_text(
        json.dumps(
            {
                "task_id": "unit-task",
                "prompt": "fix",
                "allowed_files": ["../outside.py"],
                "verifiers": [["python3", "-m", "pytest", "tests/test_a.py"]],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(runtime, "_git", lambda *_: pytest.fail("git called"))
    with pytest.raises(IssueRefused):
        runtime._fixture_task(repo)


def test_missing_canary_fails_before_running_local_worker(monkeypatch, tmp_path):
    task = {"task_id": "t", "prompt": "p", "verifiers": [], "repo": tmp_path}
    with pytest.raises(ValueError, match="--canary-report is required"):
        runtime._run_localworker(
            type("Args", (), {"state": str(tmp_path)})(),
            task,
            tmp_path,
            32768,
            DEFAULT_LOCAL_ENDPOINT,
            None,
        )


def test_missing_runs_directory_is_refused_without_creating_it(tmp_path):
    missing = tmp_path / "not-installed-runs"
    task = {"task_id": "t", "prompt": "p", "verifiers": [], "repo": tmp_path}
    canary = tmp_path / "canary.json"
    canary.write_text("report", encoding="utf-8")
    with pytest.raises(ValueError, match="runs directory does not exist"):
        runtime._run_localworker(
            type("Args", (), {"runs": missing})(),
            task,
            tmp_path,
            32768,
            DEFAULT_LOCAL_ENDPOINT,
            canary,
        )
    assert not missing.exists()


def test_local_episode_requires_completed_marker_and_record(tmp_path):
    row = {"verification": {"exit_reason": "COMPLETED"}, "spec_feedback": {"needs_spec": False}}
    episode = tmp_path / "episodes.jsonl"
    episode.write_text(json.dumps(row) + "\n", encoding="utf-8")
    assert runtime._episode_record("LOCAL_WORKER_EPISODE ERROR_ABORT id x", episode) == row
    assert runtime._episode_record("LOCAL_WORKER_EPISODE COMPLETED id x", episode) == row


def test_completed_marker_without_durable_record_is_not_success(tmp_path):
    assert (
        runtime._episode_record("LOCAL_WORKER_EPISODE COMPLETED id x", tmp_path / "missing.jsonl")
        is None
    )
