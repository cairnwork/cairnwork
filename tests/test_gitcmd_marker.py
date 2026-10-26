"""Tests for cairnwork.gitcmd context-local process markers."""

import subprocess
import sys
import threading

import pytest
from conftest import commit_change

from cairnwork import gitcmd
from cairnwork.verifiers import parse_verifier


def test_gitcmd_marked_sets_env(repo, monkeypatch):
    recorded_envs = []
    real_run = subprocess.run

    def fake_run(*args, **kwargs):
        recorded_envs.append(kwargs.get("env", {}))
        return real_run(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", fake_run)

    # Outside marked block: no marker
    gitcmd.run_git(repo, "status")
    assert "CAIRNWORK_ATTEMPT" not in recorded_envs[-1]

    # Inside marked block: marker is set
    with gitcmd.marked("CAIRNWORK_ATTEMPT", "A1"):
        gitcmd.run_git(repo, "status")
        assert recorded_envs[-1].get("CAIRNWORK_ATTEMPT") == "A1"

        # New thread started inside block: does not inherit marker
        thread_env: dict[str, str] = {}

        def worker():
            nonlocal thread_env
            gitcmd.run_git(repo, "status")
            thread_env = recorded_envs[-1]

        t = threading.Thread(target=worker)
        t.start()
        t.join()
        assert "CAIRNWORK_ATTEMPT" not in thread_env

        # Exception inside block: resets marker properly
        with pytest.raises(RuntimeError):
            with gitcmd.marked("CAIRNWORK_ATTEMPT", "A2"):
                gitcmd.run_git(repo, "status")
                assert recorded_envs[-1].get("CAIRNWORK_ATTEMPT") == "A2"
                raise RuntimeError("boom")

        gitcmd.run_git(repo, "status")
        assert recorded_envs[-1].get("CAIRNWORK_ATTEMPT") == "A1"

    # Outside block again: no marker
    gitcmd.run_git(repo, "status")
    assert "CAIRNWORK_ATTEMPT" not in recorded_envs[-1]


def test_verifiers_run_all_git_marking(project, tmp_path, monkeypatch):
    recorded_attempts = []
    real_run = subprocess.run

    def fake_run(*args, **kwargs):
        cmd = args[0] if args else kwargs.get("args", [])
        if cmd and cmd[0] == "git":
            recorded_attempts.append(kwargs.get("env", {}).get("CAIRNWORK_ATTEMPT"))
        return real_run(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", fake_run)

    spec = parse_verifier("pytest", ["python3", "-m", "pytest", "tests/test_a.py"])
    head = commit_change(project, {"tests/test_a.py": "def test_ok(): pass\n"})

    from cairnwork import pytest_config
    from cairnwork.verifiers import _run_all

    settings = pytest_config.base_settings(project, None)

    # When attempt_id is empty, no git commands carry marker
    recorded_attempts.clear()
    _run_all(
        project,
        head,
        (spec,),
        tmp_path / "runs1",
        "r1",
        sys.executable,
        60,
        "",
        None,
        settings,
        "",
    )
    assert recorded_attempts, "git must be called during checkout"
    assert all(a is None for a in recorded_attempts), "attempt_id='' must not set mark"

    # When attempt_id is non-empty, every git command during checkout & removal has marker
    recorded_attempts.clear()
    _run_all(
        project,
        head,
        (spec,),
        tmp_path / "runs2",
        "r2",
        sys.executable,
        60,
        "att123",
        None,
        settings,
        "",
    )
    assert recorded_attempts, "git must be called during checkout"
    assert all(a == "att123" for a in recorded_attempts), "all git calls must carry attempt marker"
