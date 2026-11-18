"""Hardening checks for the experimental replay runner."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

import experimental.acceptance_casebook.replay_runner as runner_module
from experimental.acceptance_casebook.replay_runner import RealRunner, isolated_env


def _uv_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "UV_INDEX_URL",
        "UV_EXTRA_INDEX_URL",
        "UV_FIND_LINKS",
        "UV_NO_BINARY",
        "UV_CONSTRAINT",
        "UV_OVERRIDE",
        "UV_CONFIG_FILE",
        "UV_PYTHON",
        "UV_OFFLINE",
    ):
        monkeypatch.setenv(key, "poison")
    monkeypatch.setenv("UV_CACHE_DIR", "/operator/cache")


def test_isolated_env_allows_only_uv_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    _uv_environment(monkeypatch)
    env = isolated_env()
    assert {key for key in env if key.startswith("UV_")} == {"UV_CACHE_DIR", "UV_NO_CONFIG"}
    assert env["UV_CACHE_DIR"] == "/operator/cache"
    assert env["UV_NO_CONFIG"] == "1"


def test_sync_and_verifier_receive_scrubbed_uv_env(tmp_path: Path, monkeypatch):
    _uv_environment(monkeypatch)
    envs: list[dict[str, str]] = []
    venv_calls: list[tuple[list[str], dict[str, Any]]] = []

    def run(argv: list[str], **kwargs: Any):
        if argv[0] == "git":
            kwargs["stdout"].write(b"")
        if argv[:2] == ["uv", "venv"]:
            venv_calls.append((argv, kwargs))
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        if kwargs.get("env") is not None:
            envs.append(kwargs["env"])
        if "tar" == argv[0]:
            Path(argv[-1], "marker").write_text("x")
        out = str(tmp_path / "cairnwork.py") if "-c" in argv else ""
        return subprocess.CompletedProcess(argv, 0, stdout=out, stderr="")

    class Popen:
        def __init__(self, _argv, **kwargs):
            envs.append(kwargs["env"])
            self.pid, self.returncode = 0, 0

        def communicate(self, timeout=None):
            return "{}", ""

    monkeypatch.setattr(runner_module.subprocess, "run", run)
    monkeypatch.setattr(runner_module.subprocess, "Popen", Popen)
    monkeypatch.setattr(runner_module, "assert_copy_import", lambda *_: None)
    monkeypatch.setattr(runner_module.tempfile, "mkdtemp", lambda **_: str(tmp_path))
    monkeypatch.setattr(runner_module.shutil, "rmtree", lambda *_a, **_k: None)
    runner = RealRunner(tmp_path, tmp_path, "v", 5)
    try:
        runner_module.run_verifier(["verify"], 5)
        ((venv_argv, venv_kwargs),) = venv_calls
        assert venv_argv[venv_argv.index("--python") + 1] == sys.executable
        venv_env = venv_kwargs["env"]
        assert "VIRTUAL_ENV" not in venv_env
        assert not {k for k in venv_env if k.startswith("UV_")} - {"UV_CACHE_DIR", "UV_NO_CONFIG"}
        sync_env, check_env, verifier_env = envs
        assert sync_env["UV_PROJECT_ENVIRONMENT"] == str(runner.venv)
        for env in (sync_env, check_env, verifier_env):
            assert env["UV_CACHE_DIR"] == "/operator/cache"
            assert env["UV_NO_CONFIG"] == "1"
            assert not {key for key in env if key.startswith("UV_")} - {
                "UV_CACHE_DIR",
                "UV_NO_CONFIG",
                "UV_PROJECT_ENVIRONMENT",
            }
    finally:
        runner.close()


def test_tar_stderr_is_suppressed_and_failure_is_generic(tmp_path: Path, monkeypatch, capfd):
    def run(argv: list[str], **kwargs: Any):
        if argv[0] == "git":
            kwargs["stdout"].write(b"")
        if argv[0] == "tar":
            assert kwargs.get("capture_output") is True
            raise subprocess.CalledProcessError(2, argv, stderr=b"::error::untrusted tar output")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(runner_module.subprocess, "run", run)
    monkeypatch.setattr(runner_module.tempfile, "mkdtemp", lambda **_: str(tmp_path))
    with pytest.raises(runner_module.RunnerFailure, match="VERSION_ENV"):
        RealRunner(tmp_path, tmp_path, "v", 5)
    captured = capfd.readouterr()
    assert "::error::" not in captured.out + captured.err
