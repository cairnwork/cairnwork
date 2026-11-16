"""Focused guards for mutants that survived the previous review round."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from experimental.pause.core_judge import CoreJudge
from experimental.pause.flow import RunResult, _scope_ok
from experimental.pause.runner import _prepare


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def repo(root: Path) -> tuple[Path, str]:
    root.mkdir(parents=True)
    git(root, "init", "-q")
    (root / "src").mkdir()
    (root / "src/a.py").write_text("base\n")
    git(root, "add", ".")
    git(root, "-c", "user.name=t", "-c", "user.email=t@invalid", "commit", "-qm", "base")
    return root, git(root, "rev-parse", "HEAD")


def test_flag_off_does_not_import_core_verification_chain() -> None:
    code = (
        "import sys; from experimental.pause import cli; "
        "assert cli.main(['run','--unknown'], worker_main=lambda _a: 9) == 9; "
        "assert 'experimental.pause.core_judge' not in sys.modules; "
        "assert 'cairnwork.verify_candidate' not in sys.modules"
    )
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(p for p in sys.path if p)}
    subprocess.run([sys.executable, "-c", code], check=True, env=env)


def test_hardened_git_environment_removes_inherited_git_controls(monkeypatch: Any) -> None:
    from experimental.pause.git_env import hardened_git_env

    monkeypatch.setenv("GIT_INDEX_FILE", "/untrusted/index")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/untrusted/config")
    monkeypatch.setenv("GIT_TRACE", "1")
    env = hardened_git_env()
    assert not any(
        key.startswith("GIT_")
        for key in env
        if key
        not in {
            "GIT_CONFIG_GLOBAL",
            "GIT_CONFIG_NOSYSTEM",
            "GIT_NO_REPLACE_OBJECTS",
        }
    )
    assert env["GIT_CONFIG_GLOBAL"] == "/dev/null"
    assert env["GIT_CONFIG_NOSYSTEM"] == "1" and env["GIT_NO_REPLACE_OBJECTS"] == "1"


def test_core_judge_allowed_files_are_diffed_from_pinned_base(tmp_path: Path) -> None:
    checkout, base = repo(tmp_path / "checkout")
    (checkout / "src/a.py").write_text("R1 committed edit\n")
    git(checkout, "add", "src/a.py")
    git(checkout, "-c", "user.name=t", "-c", "user.email=t@invalid", "commit", "-qm", "R1")
    seen: dict[str, Any] = {}
    judge = CoreJudge(
        tmp_path / "state",
        (),
        verify=lambda _intake, **kwargs: seen.update(kwargs) or {"status": "FAILED"},
    )
    judge(RunResult(0, "", tmp_path, checkout, None, {}, base_sha=base))
    assert set(seen["allowed_files"]) == {"src/a.py"}


def test_scope_raw_diff_is_pinned_to_base_for_committed_symlink(tmp_path: Path) -> None:
    checkout, base = repo(tmp_path / "checkout")
    (checkout / "src/link").symlink_to("a.py")
    git(checkout, "add", "src/link")
    git(checkout, "-c", "user.name=t", "-c", "user.email=t@invalid", "commit", "-qm", "symlink")
    assert not _scope_ok(checkout, ["src"], base)


@pytest.mark.parametrize("failure", ["dirty", "wrong-head"])
def test_prepare_checks_fresh_checkout_invariants(
    tmp_path: Path, monkeypatch: Any, failure: str
) -> None:
    source, base = repo(tmp_path / "source")
    checkout = tmp_path / "fresh" / "checkout"
    real_git = __import__("experimental.pause.runner", fromlist=["_git"])._git

    def fake_git(path: Path, *args: str) -> str:
        if failure == "dirty" and args == ("status", "--porcelain", "--ignored"):
            return "?? stale"
        if failure == "wrong-head" and args == ("rev-parse", "HEAD"):
            return "0" * 40
        return real_git(path, *args)

    monkeypatch.setattr("experimental.pause.runner._git", fake_git)
    with pytest.raises(RuntimeError, match="fresh R1 checkout|parent base"):
        _prepare(source, checkout, base)


def test_parse_worker_output_rejects_valid_record_with_wrong_episode(tmp_path: Path) -> None:
    from experimental.pause.runner import _parse_worker_output

    out = tmp_path / "out"
    out.mkdir()
    eid = "ep_line"
    record = {"episode_id": "ep_record", "verification": {"exit_reason": "COMPLETED"}}
    line = json.dumps(record, separators=(",", ":")) + "\n"
    corpus = out / "episodes.jsonl"
    corpus.write_text(line)
    transcript = out / "private" / eid / "transcript.json"
    transcript.parent.mkdir(parents=True)
    transcript.write_text("{}")
    import hashlib

    digest = hashlib.sha256(line.encode()).hexdigest()
    output = f"LOCAL_WORKER_EPISODE COMPLETED {eid} {corpus} line_sha256={digest}\n"
    result = _parse_worker_output(output, 0, tmp_path, tmp_path / "checkout", out)
    assert result.record is None and result.transcript is None


def test_configured_core_verifiers_require_pair_shape(tmp_path: Path) -> None:
    from experimental.pause.cli import _configured_run

    client = {
        "model_id": "m",
        "effort": "low",
        "timeout_seconds": 1,
        "max_attempts": 1,
        "transport_command": ["transport"],
    }
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "planner": client,
                "gate": client,
                "core": {"state_dir": str(tmp_path / "state"), "verifiers": ["malformed"]},
            }
        )
    )
    with pytest.raises(ValueError, match="core.verifiers"):
        _configured_run(str(config))


def test_configured_core_is_created_and_passed_into_flow(tmp_path: Path, monkeypatch) -> None:
    from experimental.pause import cli

    client = {
        "model_id": "m",
        "effort": "low",
        "timeout_seconds": 1,
        "max_attempts": 1,
        "transport_command": ["transport"],
    }
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "planner": client,
                "gate": client,
                "core": {"state_dir": str(tmp_path / "state"), "verifiers": []},
            }
        )
    )
    task = tmp_path / "task.json"
    task.write_text('{"task_id":"task"}')
    observed: dict[str, Any] = {}

    class Result:
        output = "ok\n"
        code = 0

    monkeypatch.setattr(cli, "run_flow", lambda *_a, **kw: (observed.update(kw) or Result(), {}))
    monkeypatch.setattr(cli.receipt_module, "append", lambda *_a: None)
    code = cli.main(
        [
            "run",
            "--planner-config",
            str(config),
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
        ]
    )
    assert code == 0 and isinstance(observed["core_verdict"], CoreJudge)
