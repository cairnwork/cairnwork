"""Offline negative coverage for local relay worker setup and failure visibility."""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

from experimental.local_relay import runtime
from experimental.local_relay.adapters import LocalWorkerAdapter
from experimental.local_relay.config import DEFAULT_LOCAL_ENDPOINT
from experimental.local_relay.diagnostics import MAX_STDERR_CHARS, append_debug, episode_failure
from experimental.local_relay.relay import LocalAttemptFailed
from experimental.pause.flow import RunResult

_RESTORE_CALLS: list = []


def _local_run_fakes(monkeypatch, tmp_path, marker, stderr=""):
    captured = {}
    canary = tmp_path / "canary.json"
    canary.write_text("report", encoding="utf-8")
    Path(str(canary) + ".sha256").write_text("digest", encoding="utf-8")
    monkeypatch.setattr(runtime, "_git", lambda *_args: "a" * 40)

    def prepare(_source, checkout, _base):
        checkout.mkdir()

    monkeypatch.setattr(runtime, "_prepare", prepare)
    monkeypatch.setattr(runtime, "prepare_worker_layout", lambda *_args: None)
    monkeypatch.setattr(runtime, "restore_judge_modes", lambda *args: _RESTORE_CALLS.append(args))

    def launch(argv, _cwd=None, **_kwargs):
        captured["argv"] = argv
        out = Path(argv[argv.index("--out") + 1])
        episode = out / "episodes-cairnwork-qwen3.5_9b-pilot.jsonl"
        row = {
            "verification": {"exit_reason": marker.split()[1]},
            "spec_feedback": {"needs_spec": False},
        }
        episode.write_text(json.dumps(row) + "\n", encoding="utf-8")
        return type("Result", (), {"returncode": 0, "stdout": marker, "stderr": stderr})()

    monkeypatch.setattr(runtime, "_spawn_worker", launch)
    monkeypatch.setattr(runtime, "executable_paths", lambda *_a: frozenset())
    runs = tmp_path / "runs"
    runs.mkdir()
    args = type(
        "Args",
        (),
        {
            "runs": runs,
            "ledger": str(tmp_path / "ledger.jsonl"),
        },
    )()
    task = {
        "task_id": "task-1",
        "prompt": "fix",
        "verifiers": [("pytest-0", ("python3", "-m", "pytest", "-q", "tests/test_a.py"))],
        "allowed_files": ["src/a.py", "tests/test_a.py"],
        "base_commit": "a" * 40,
        "repo": tmp_path / "repo",
    }
    result = runtime._run_localworker(
        args, task, tmp_path / "repo", 65536, DEFAULT_LOCAL_ENDPOINT, canary
    )
    return result, captured["argv"]


def test_worker_command_uses_context_endpoint_and_write_scope(monkeypatch, tmp_path):
    _result, argv = _local_run_fakes(
        monkeypatch, tmp_path, "LOCAL_WORKER_EPISODE COMPLETED episode path line_sha256=x"
    )
    assert argv[argv.index("--context-cap") + 1] == "65536"
    assert argv[argv.index("--endpoint") + 1] == DEFAULT_LOCAL_ENDPOINT
    assert argv[argv.index("--runs") + 1] == str((tmp_path / "runs").resolve())
    run_root = Path(argv[argv.index("--checkout") + 1]).parent
    assert run_root.parent == (tmp_path / "runs").resolve()
    assert len(run_root.name) == 32 and all(char in "0123456789abcdef" for char in run_root.name)
    assert run_root.stat().st_mode & 0o777 == 0o750
    assert argv.count("--write-path") == 2
    assert argv[argv.index("--write-path") + 1] == "src/a.py"


def test_worker_abort_record_cannot_become_candidate(monkeypatch, tmp_path):
    result, _argv = _local_run_fakes(
        monkeypatch, tmp_path, "LOCAL_WORKER_EPISODE ERROR_ABORT episode path line_sha256=x"
    )
    assert result.code == 0
    assert isinstance(result.record, dict)
    assert result.record["verification"]["exit_reason"] == "ERROR_ABORT"
    with pytest.raises(LocalAttemptFailed, match="episode_status=ERROR_ABORT"):
        LocalWorkerAdapter(lambda *_: result).run_with_endpoint(32768, DEFAULT_LOCAL_ENDPOINT)


def test_completed_episode_does_not_forward_worker_stderr(monkeypatch, tmp_path):
    result, _argv = _local_run_fakes(
        monkeypatch,
        tmp_path,
        "LOCAL_WORKER_EPISODE COMPLETED episode path line_sha256=x",
        "api_key=private-value",
    )
    assert "private-value" not in result.output
    assert isinstance(result.record, dict)
    assert "_local_relay_diagnostics" not in result.record


def test_debug_log_records_invocation_paths_and_is_private(monkeypatch, tmp_path):
    result, _argv = _local_run_fakes(
        monkeypatch, tmp_path, "LOCAL_WORKER_EPISODE COMPLETED episode path line_sha256=x"
    )
    debug = tmp_path / "relay-debug-task-1.log"
    assert result.code == 0
    assert debug.exists() and stat.S_IMODE(debug.stat().st_mode) == 0o600
    records = [json.loads(line) for line in debug.read_text().splitlines()]
    started = next(row for row in records if row["event"] == "worker-start")
    assert started["cwd"] != str((tmp_path / "repo").resolve())
    assert (Path(started["cwd"]) / "experimental" / "local_relay").is_dir()
    assert started["run_root"].endswith("/checkout") is False
    assert started["checkout"].endswith("/checkout")
    assert "--context-cap" in started["argv"] and "--endpoint" in started["argv"]


def test_worker_failure_keeps_process_and_episode_diagnostics(monkeypatch, tmp_path):
    result, _argv = _local_run_fakes(
        monkeypatch, tmp_path, "LOCAL_WORKER_EPISODE CONTEXT_LIMIT episode path line_sha256=x"
    )
    assert result.code == 0
    assert isinstance(result.record, dict)
    assert result.record["_local_relay_diagnostics"]["episode_status"] == "CONTEXT_LIMIT"
    assert result.record["_local_relay_diagnostics"]["process_exit_code"] == 0
    with pytest.raises(LocalAttemptFailed, match="episode_status=CONTEXT_LIMIT"):
        LocalWorkerAdapter(lambda *_: result).run_with_endpoint(32768, DEFAULT_LOCAL_ENDPOINT)


def test_debug_log_redacts_credentials(tmp_path):
    path = tmp_path / "relay-debug-task.log"
    append_debug(path, {"argv": ["--api-key", "ghp_abcdefghijklmno"]})
    assert "ghp_abcdefghijklmno" not in path.read_text()
    assert "[REDACTED]" in path.read_text()


def test_failure_reason_bounds_and_redacts_worker_stderr():
    secret = "ghp_abcdefghijklmno"
    details = runtime._worker_diagnostics(
        2, "", f"Authorization: Bearer {secret}\n" + "x" * 3000, None
    )
    result = RunResult(
        2, "", Path("/run"), Path("/checkout"), record={"_local_relay_diagnostics": details}
    )
    reason = episode_failure(result)
    assert details["episode_status"] == "NO_EPISODE_RECORD"
    assert len(details["stderr"]) == MAX_STDERR_CHARS
    assert secret not in details["stderr"] and secret not in reason
    assert "process_exit=2" in reason and "[REDACTED]" in reason


def test_worker_layout_matches_wrapper_directories_and_write_modes(monkeypatch, tmp_path):
    from experimental.local_relay import worker_setup

    root = tmp_path / "root"
    root.mkdir(mode=0o750)
    checkout = root / "checkout"
    checkout.mkdir()
    (checkout / "tracked.py").write_text("pass\n", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("keep mode\n", encoding="utf-8")
    (checkout / "linked.txt").symlink_to(outside)
    monkeypatch.setattr(worker_setup, "_worker_python", lambda: "/tool/python3.13")
    commands = []
    monkeypatch.setattr(
        worker_setup.subprocess,
        "run",
        lambda argv, **kwargs: commands.append((argv, kwargs)),
    )

    worker_setup.prepare_worker_layout(root, checkout)

    assert (root / "tmp").stat().st_mode & 0o777 == 0o770
    assert (root / "q").stat().st_mode & 0o777 == 0o770
    assert checkout.stat().st_mode & 0o777 == 0o770
    assert (checkout / "tracked.py").stat().st_mode & 0o777 == 0o660
    assert stat.S_IMODE(outside.stat().st_mode) == 0o644
    assert (root / "venv").name == "venv"
    assert [row[0][1] for row in commands] == ["venv", "pip"]


def test_worker_layout_refuses_missing_worker_python(monkeypatch, tmp_path):
    from experimental.local_relay import worker_setup

    root, checkout = tmp_path / "root", tmp_path / "root" / "checkout"
    root.mkdir()
    checkout.mkdir()
    monkeypatch.setattr(
        worker_setup,
        "_worker_python",
        lambda: (_ for _ in ()).throw(RuntimeError("worker Python missing")),
    )
    with pytest.raises(RuntimeError, match="worker Python missing"):
        worker_setup.prepare_worker_layout(root, checkout)
    assert not (root / "venv").exists()


def test_redaction_covers_quoted_bearer_and_common_token_prefixes():
    from experimental.local_relay.diagnostics import redact

    samples = [
        'api_key="abc123SECRETvalue"',
        "{'token': 'abc123SECRETvalue'}",
        "Authorization: Bearer abc123SECRETvalue",
        "bearer abc123SECRETvalue",
        "github_pat_abc123SECRETvalue",
        "xoxb-abc123SECRETvalue",
        "AKIAABCDEFGHIJKLMNOP",
        "eyJhbGciOiJIUzI1.eyJzdWIiOiIxMjM0.abcSIGNATURE",
    ]
    for sample in samples:
        assert "SECRET" not in redact(sample) and "ABCDEFGHIJKLMNOP" not in redact(sample)
        assert "abcSIGNATURE" not in redact(sample)
    assert redact("plain failure text") == "plain failure text"


def test_worker_layout_regroups_checkout_scratch_and_venv_to_the_runs_gid(monkeypatch, tmp_path):
    import os

    from experimental.local_relay import worker_setup

    others = [g for g in os.getgroups() if g != os.getgid()]
    gid = others[0] if others else os.getgid()
    runs = tmp_path / "runs"
    runs.mkdir()
    os.chown(runs, -1, gid)
    root = runs / ("a" * 32)
    root.mkdir(mode=0o750)
    checkout = root / "checkout"
    (checkout / ".git" / "hooks").mkdir(parents=True)
    (checkout / "tracked.py").write_text("pass\n", encoding="utf-8")
    monkeypatch.setattr(worker_setup, "_worker_python", lambda: "/tool/python3.13")

    def fake_run(argv, **_kwargs):
        if argv[1] == "venv":
            (root / "venv" / "bin").mkdir(parents=True)
            (root / "venv" / "bin" / "python").write_text("", encoding="utf-8")

    monkeypatch.setattr(worker_setup.subprocess, "run", fake_run)

    worker_setup.prepare_worker_layout(root, checkout)

    for path in (
        root / "tmp",
        root / "q",
        checkout,
        checkout / ".git",
        checkout / ".git" / "hooks",
        checkout / "tracked.py",
        root / "venv",
        root / "venv" / "bin" / "python",
    ):
        assert path.stat().st_gid == gid, path


def _git_checkout(tmp_path):
    import subprocess

    repo = tmp_path / "co"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg" / "a.py").write_text("x = 1\n", encoding="utf-8")
    (repo / "run.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (repo / "run.sh").chmod(0o755)
    env = {"GIT_CONFIG_GLOBAL": "/dev/null", "PATH": os.environ["PATH"], "HOME": str(tmp_path)}
    for argv in (
        ["init", "-q"],
        ["add", "-A"],
        ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "base"],
    ):
        subprocess.run(["git", "-C", str(repo), *argv], check=True, env=env)
    base = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, env=env
    ).stdout.strip()
    return repo, base


def test_restore_judge_modes_returns_core_shape_and_runs_no_repo_controlled_code(tmp_path):
    from experimental.local_relay import worker_setup

    repo, base = _git_checkout(tmp_path)
    marker = tmp_path / "executed"
    hook = tmp_path / "fsmon.sh"
    hook.write_text(f"#!/bin/sh\ntouch {marker}\n", encoding="utf-8")
    hook.chmod(0o755)
    with (repo / ".git" / "config").open("a", encoding="utf-8") as handle:
        handle.write(f"[core]\n\tfsmonitor = {hook}\n")
    worker_setup._set_checkout_modes(repo, os.getegid())
    (repo / "run.sh").chmod(0o660)  # worker mode must not be trusted as the executable bit
    os.chmod(repo / "pkg" / "a.py", 0o770)  # a file the worker tried to make executable
    (repo / "pkg" / "new.py").write_text("y = 2\n", encoding="utf-8")
    os.symlink("a.py", repo / "pkg" / "link.py")

    worker_setup.restore_judge_modes(repo, worker_setup.executable_paths(repo, base))

    assert stat.S_IMODE(repo.stat().st_mode) == 0o755
    assert stat.S_IMODE((repo / "pkg").stat().st_mode) == 0o755
    assert stat.S_IMODE((repo / "run.sh").stat().st_mode) == 0o755  # executable in base
    assert stat.S_IMODE((repo / "pkg" / "a.py").stat().st_mode) == 0o644  # not executable in base
    assert stat.S_IMODE((repo / "pkg" / "new.py").stat().st_mode) == 0o644
    assert (repo / "pkg" / "a.py").stat().st_gid == os.getegid()
    assert (repo / "pkg" / "link.py").is_symlink()
    assert not marker.exists()


def test_restore_judge_modes_fails_closed_when_an_entry_cannot_be_reowned(monkeypatch, tmp_path):
    from experimental.local_relay import worker_setup

    repo, base = _git_checkout(tmp_path)

    def refuse(*_args, **_kwargs):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(worker_setup.os, "fchown", refuse)
    with pytest.raises(RuntimeError, match="cannot restore judge modes"):
        worker_setup.restore_judge_modes(repo, frozenset())


def test_worker_checkout_modes_are_restored_for_core_after_the_worker_exits(monkeypatch, tmp_path):
    _RESTORE_CALLS.clear()
    _local_run_fakes(monkeypatch, tmp_path, "EPISODE COMPLETE")
    assert len(_RESTORE_CALLS) == 1
    checkout, executable = _RESTORE_CALLS[0]
    assert checkout.name == "checkout" and executable == frozenset()


def test_restore_strips_worker_group_write_from_git_dir_and_rejects_hard_links(tmp_path):
    from experimental.local_relay import worker_setup

    repo, base = _git_checkout(tmp_path)
    worker_setup._set_checkout_modes(repo, os.getegid())
    git_file = repo / ".git" / "HEAD"
    assert stat.S_IMODE(git_file.stat().st_mode) == 0o660
    worker_setup.restore_judge_modes(repo, worker_setup.executable_paths(repo, base))
    assert stat.S_IMODE(git_file.stat().st_mode) == 0o644
    assert stat.S_IMODE((repo / ".git").stat().st_mode) == 0o755
    outside = tmp_path / "outside.txt"
    outside.write_text("planted\n", encoding="utf-8")
    os.link(outside, repo / "pkg" / "hard.py")
    with pytest.raises(RuntimeError, match="hard link"):
        worker_setup.restore_judge_modes(repo, frozenset())
    assert stat.S_IMODE(outside.stat().st_mode) == 0o644  # untouched


def test_prepare_refuses_hard_linked_checkout_files_before_the_worker_runs(tmp_path):
    from experimental.local_relay import worker_setup

    repo, _base = _git_checkout(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("planted\n", encoding="utf-8")
    os.link(outside, repo / "pkg" / "hard.py")
    with pytest.raises(RuntimeError, match="hard-linked"):
        worker_setup._set_checkout_modes(repo, os.getegid())
    assert stat.S_IMODE(outside.stat().st_mode) == 0o644


def test_executable_set_comes_from_the_trusted_repo_not_the_checkout_index(tmp_path):
    from experimental.local_relay import worker_setup

    repo, base = _git_checkout(tmp_path)
    assert worker_setup.executable_paths(repo, base) == frozenset({b"run.sh"})


def test_worker_process_group_is_killed_after_the_episode(tmp_path):
    import signal  # noqa: F401
    import time

    pid_file = tmp_path / "child.pid"
    code = (
        "import subprocess,sys\n"
        "p=subprocess.Popen(['sleep','30'])\n"
        f"open({str(pid_file)!r},'w').write(str(p.pid))\n"
    )
    result = runtime._spawn_worker([sys.executable, "-c", code], tmp_path)
    assert result.returncode == 0
    pid = int(pid_file.read_text())
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
