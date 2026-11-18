"""Issue #751 step 3b: the real replay runner (verdict mapping, argv, isolation, inputs)."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest
from test_acceptance_casebook_replay import _args, _confirmed

import experimental.acceptance_casebook.replay_runner as runner_module
from experimental.acceptance_casebook import replay
from experimental.acceptance_casebook.replay_runner import RealRunner, RunnerFailure
from experimental.acceptance_casebook.store import CasebookError

pytest_plugins = ["test_acceptance_casebook_origin"]

CASE = {"base": "a" * 40, "candidate": "b" * 40}
NO_ARGS: dict[str, Any] = {"allowed_file": [], "verifier": []}


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def _identity_env() -> dict[str, str]:
    return {
        **os.environ,
        "GIT_AUTHOR_NAME": "fixture",
        "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
        "GIT_COMMITTER_NAME": "fixture",
        "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
    }


def _bound_case() -> tuple[dict[str, Any], dict[str, Any]]:
    case = {"line_sha256": "a" * 64, "base": "b" * 40, "candidate": "c" * 40}
    request = {
        "acceptance_contract": {"allowed_paths": ["x.py"]},
        "verification_plan": {"required_verifier_ids": ["pytest-0"]},
    }
    return case, request


def _bound(raw_extra: dict[str, Any], request_extra: dict[str, Any]) -> dict[str, Any]:
    case, request = _bound_case()
    raw = {"allowed_file": ["x.py"], "verifier": ["pytest -q"], **raw_extra}
    return replay._bound_inputs(case, {case["line_sha256"]: raw}, {**request, **request_extra})


def test_bound_inputs_accepts_equal_optional_values():
    raw = {
        "test_commit": "",
        "import_path": [],
        "blackbox": None,
        "require_protection": False,
    }
    assert _bound(raw, {})["test_commit"] == ""
    assert _bound({}, {})["allowed_file"] == ["x.py"]


def test_bound_inputs_accepts_a_non_default_value_the_request_binds():
    sha = "d" * 40
    raw = {"test_commit": sha, "import_path": ["src"], "require_protection": True}
    bound = {"test_commit": sha, "import_paths": ["src"], "require_protection": True}
    assert _bound(raw, bound)["test_commit"] == sha


@pytest.mark.parametrize(
    "key,value,request_extra",
    [
        ("test_commit", 3, {}),
        ("import_path", "src", {}),
        ("blackbox", False, {}),
        ("require_protection", 1, {}),
        ("test_commit", "d" * 40, {}),  # the request does not bind a test commit
        ("import_path", ["src"], {}),
        ("blackbox", "decl.json", {}),
        ("require_protection", True, {}),
        ("test_commit", "d" * 40, {"test_commit": "e" * 40}),  # the request binds another one
        ("import_path", ["src"], {"import_paths": ["lib"]}),
        ("blackbox", "decl.json", {"blackbox": "other.json"}),
        ("require_protection", False, {"require_protection": True}),
    ],
)
def test_bound_inputs_rejects_unbound_or_wrong_optional_values(key, value, request_extra):
    with pytest.raises(CasebookError, match="REPLAY_INPUT_MISMATCH"):
        _bound({key: value}, request_extra)


def test_bound_inputs_rejects_unknown_keys_and_a_changed_verifier():
    with pytest.raises(CasebookError, match="REPLAY_INPUT_MISMATCH"):
        _bound({"surprise": 1}, {})
    case, request = _bound_case()
    raw = {"allowed_file": ["x.py"], "verifier": []}
    with pytest.raises(CasebookError, match="REPLAY_INPUT_MISMATCH"):
        replay._bound_inputs(case, {case["line_sha256"]: raw}, request)


def test_real_runner_cli_uses_single_equals_arguments(tmp_path):
    case = {"base": "a" * 40, "candidate": "b" * 40}
    argv = runner_module.build_argv(
        tmp_path / "venv",
        tmp_path / "state",
        tmp_path / "repo",
        case,
        {
            "allowed_file": ["--state=/x", "a b"],
            "verifier": ["pytest -q"],
            "import_path": ["src"],
            "blackbox": "decl.json",
            "require_protection": True,
        },
    )
    assert "--allowed-file=--state=/x" in argv and "--allowed-file=a b" in argv
    assert "--verifier=pytest -q" in argv and "--import-path=src" in argv
    assert "--blackbox=decl.json" in argv and "--require-protection" in argv
    assert "--test-commit=" in argv and argv[0].endswith("bin/cairnwork-verify")
    bare = [i for i in argv if i.startswith("--") and "=" not in i]
    assert bare == ["--require-protection"]


def test_real_runner_rejects_import_outside_archived_copy(tmp_path):
    copy = tmp_path / "copy"
    copy.mkdir()
    inside = copy / "src" / "cairnwork" / "__init__.py"
    inside.parent.mkdir(parents=True)
    inside.touch()
    runner_module.assert_copy_import(str(inside), copy)
    with pytest.raises(ValueError):
        runner_module.assert_copy_import(str(Path(__file__).resolve()), copy)


def _fake_runner(tmp_path: Path, script_body: str, timeout: int = 2) -> RealRunner:
    """A runner whose venv holds a stub cairnwork-verify; git clone is stubbed to a mkdir."""
    runner = RealRunner.__new__(RealRunner)
    runner.root, runner.repo, runner.timeout = tmp_path, tmp_path / "repo", timeout
    runner.repo.mkdir()
    runner.venv = tmp_path / "venv"
    (runner.venv / "bin").mkdir(parents=True)
    script = runner.venv / "bin" / "cairnwork-verify"
    script.write_text("#!/bin/sh\n" + script_body)
    script.chmod(0o755)
    return runner


def _stub_git(monkeypatch) -> None:
    original = subprocess.run

    def run(argv, **kwargs):
        if argv[0] == "git":
            if "clone" in argv:
                Path(argv[-1]).mkdir()
            return subprocess.CompletedProcess(argv, 0)
        return original(argv, **kwargs)

    monkeypatch.setattr(runner_module.subprocess, "run", run)


def _verdict_case(status, verified, core, code):
    return ({"status": status, "verified": verified, "core_status": core}, code)


V, F = "VERIFIED", "FAILED"


@pytest.mark.parametrize(
    "payload,code,expected,reason",
    [
        (*_verdict_case(V, True, V, 0), "accepted", None),
        (*_verdict_case(F, False, F, 2), "refused", None),
        # accepted needs exit 0 and verified True and status VERIFIED and core VERIFIED
        (*_verdict_case(V, True, V, 2), None, "NO_CORE_VERDICT"),
        (*_verdict_case(V, True, V, 1), None, "NO_CORE_VERDICT"),
        (*_verdict_case(V, False, V, 0), None, "NO_CORE_VERDICT"),
        (*_verdict_case(V, 1, V, 0), None, "NO_CORE_VERDICT"),
        (*_verdict_case(V, True, F, 0), None, "NO_CORE_VERDICT"),
        (*_verdict_case(V, True, "", 0), None, "NO_CORE_VERDICT"),
        (*_verdict_case(V, True, None, 0), None, "NO_CORE_VERDICT"),
        (*_verdict_case(F, True, V, 0), None, "NO_CORE_VERDICT"),
        # refused needs exit 2 and verified False and core FAILED
        (*_verdict_case(F, False, "", 2), None, "NO_CORE_VERDICT"),
        (*_verdict_case(F, False, None, 2), None, "NO_CORE_VERDICT"),
        (*_verdict_case(F, False, V, 2), None, "NO_CORE_VERDICT"),
        (*_verdict_case(F, False, F, 0), None, "NO_CORE_VERDICT"),
        (*_verdict_case(F, False, F, 1), None, "NO_CORE_VERDICT"),
        (*_verdict_case(F, True, F, 2), None, "NO_CORE_VERDICT"),
        (*_verdict_case(F, 0, F, 2), None, "NO_CORE_VERDICT"),
        (*_verdict_case("INTERNAL", False, "", 1), None, "NO_CORE_VERDICT"),
        ({"error": "bad"}, 2, None, "NO_CORE_VERDICT"),
        (*_verdict_case("REFUSED", False, "", 2), None, "PRE_CORE_REFUSED"),
        (*_verdict_case("UNCOMPARABLE", False, "", 2), None, "PRE_CORE_UNCOMPARABLE"),
        ("not-json", 1, None, "UNREADABLE_OUTPUT"),
        ("", 0, None, "UNREADABLE_OUTPUT"),
        (["not", "object"], 1, None, "UNREADABLE_OUTPUT"),
    ],
)
def test_runner_verdict_requires_core_verdict(
    tmp_path, monkeypatch, payload, code, expected, reason
):
    output = payload if isinstance(payload, str) else json.dumps(payload)
    runner = _fake_runner(tmp_path, f"printf '%s' {json.dumps(output)}\nexit {code}\n")
    _stub_git(monkeypatch)
    if reason:
        with pytest.raises(RunnerFailure) as caught:
            runner.run(CASE, NO_ARGS)
        assert caught.value.reason == reason
    else:
        result = runner.run(CASE, NO_ARGS)
        assert result.verdict == expected and result.exit_code == code


@pytest.mark.parametrize(
    "value,mapped,reason",
    [
        ({"exit_code": 0, "verdict": "accepted"}, "UNREPLAYABLE", "UNREADABLE_OUTPUT"),
        ({"exit_code": 2, "verdict": "refused"}, "UNREPLAYABLE", "UNREADABLE_OUTPUT"),
        ({"exit_code": 2, "verdict": "accepted"}, "UNREPLAYABLE", "UNREADABLE_OUTPUT"),
        ({"exit_code": 1, "verdict": "refused"}, "UNREPLAYABLE", "UNREADABLE_OUTPUT"),
        ({"exit_code": 0, "verdict": "refused"}, "UNREPLAYABLE", "UNREADABLE_OUTPUT"),
        ({"exit_code": 0, "verdict": "FAILED"}, "refused", ""),  # legacy injected-runner shape
    ],
)
def test_run_one_maps_real_runner_verdicts_by_exit_code(monkeypatch, value, mapped, reason):
    monkeypatch.setattr(replay, "RUNNER", lambda *_: value)
    got, why, code, _ = replay._run_one({"kind": "accepted_bad"}, {}, 0)
    assert (got, why) == (mapped, reason)
    if mapped != "UNREPLAYABLE":
        assert code == value["exit_code"]  # type: ignore[index]


@pytest.mark.parametrize("why", ["COMMIT_MISSING", "PRE_CORE_REFUSED", "VERSION_ENV", "TIMEOUT"])
def test_run_one_keeps_the_runner_failure_reason(monkeypatch, why):
    def failing(*_):
        raise RunnerFailure(why)

    monkeypatch.setattr(replay, "RUNNER", failing)
    assert replay._run_one({"kind": "accepted_bad"}, {}, 0)[:2] == ("UNREPLAYABLE", why)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_runner_timeout_kills_the_whole_process_group(tmp_path, monkeypatch):
    pidfile = tmp_path / "child.pid"
    runner = _fake_runner(tmp_path, f"sleep 60 &\necho $! > {pidfile}\nsleep 60\n", timeout=1)
    _stub_git(monkeypatch)
    with pytest.raises(RunnerFailure) as caught:
        runner.run(CASE, NO_ARGS)
    assert caught.value.reason == "TIMEOUT"
    pid = int(pidfile.read_text())
    try:
        deadline = time.monotonic() + 5
        while _alive(pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not _alive(pid), "a grandchild of the verifier survived the timeout"
    finally:
        if _alive(pid):
            os.kill(pid, signal.SIGKILL)


def test_a_group_that_is_already_gone_at_timeout_is_still_a_timeout(tmp_path, monkeypatch):
    runner = _fake_runner(tmp_path, "sleep 30\n", timeout=1)
    _stub_git(monkeypatch)
    real_killpg = os.killpg

    def killpg_then_gone(pid, sig):
        real_killpg(pid, sig)
        raise ProcessLookupError

    monkeypatch.setattr(runner_module.os, "killpg", killpg_then_gone)
    with pytest.raises(RunnerFailure) as caught:
        runner.run(CASE, NO_ARGS)
    assert caught.value.reason == "TIMEOUT"


def test_missing_commit_stops_before_the_verifier(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    runner = RealRunner.__new__(RealRunner)
    runner.repo, runner.timeout, runner.root = repo, 5, tmp_path
    runner.venv = tmp_path / "venv"
    called: list[list[str]] = []
    original = subprocess.run

    def observe(argv, **kwargs):
        called.append(argv)
        return original(argv, **kwargs)

    monkeypatch.setattr(runner_module.subprocess, "run", observe)
    with pytest.raises(RunnerFailure) as caught:
        runner.run(CASE, {"allowed_file": ["x"], "verifier": ["true"]})
    assert caught.value.reason == "COMMIT_MISSING"
    assert called[0][:2] == ["git", "clone"] and called[1][3] == "cat-file"
    assert "cairnwork-verify" not in str(called)


def test_commits_are_checked_in_the_per_case_clone_not_the_operator_repo(tmp_path):
    repo = tmp_path / "source"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "tracked").write_text("base\n")
    subprocess.run(["git", "-C", str(repo), "add", "tracked"], check=True)
    env = _identity_env()
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "base"], env=env, check=True)
    base = _git(repo, "rev-parse", "HEAD")
    dangling = subprocess.check_output(
        ["git", "-C", str(repo), "commit-tree", base + "^{tree}", "-m", "dangling"],
        text=True,
        env=env,
    ).strip()
    assert _git(repo, "cat-file", "-t", dangling) == "commit"  # present in the operator repo
    marker = tmp_path / "started"
    runner = _fake_runner(tmp_path, f"touch {marker}\nexit 0\n", timeout=10)
    runner.repo = repo
    with pytest.raises(RunnerFailure) as caught:
        runner.run({"base": base, "candidate": dangling}, NO_ARGS)
    assert caught.value.reason == "COMMIT_MISSING"
    assert not marker.exists()  # cairnwork-verify never started


def _replay_args(tmp_path, lg, capsys, repo):
    cases = [
        _confirmed(lg, capsys, "a1", "accepted_bad"),
        _confirmed(lg, capsys, "a2", "refused_good"),
    ]
    args = _args(tmp_path, lg, cases) + ["--repo", str(repo)]
    return args, Path(args[args.index("--out") + 1])


def _rows(out: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in out.read_text().splitlines()][:-1]


def test_setup_failure_is_version_env_and_recorded_unreplayable(
    monkeypatch, tmp_path, lg, capsys, repo
):
    args, out = _replay_args(tmp_path, lg, capsys, repo)
    roots = []
    original_mkdtemp = runner_module.tempfile.mkdtemp

    def track_root(*a, **kw):
        roots.append(Path(original_mkdtemp(*a, **kw)))
        return str(roots[-1])

    monkeypatch.setattr(runner_module.tempfile, "mkdtemp", track_root)
    monkeypatch.setattr(replay, "RUNNER", replay._DEFAULT_RUNNER)
    assert replay.main(args) == 3  # the archived fixture version has no installable project
    rows = _rows(out)
    assert len(rows) == 2
    assert all(r["outcome"] == "UNREPLAYABLE" and r["reason"] == "VERSION_ENV" for r in rows)
    assert roots and all(not root.exists() for root in roots)


def test_a_constructor_error_is_version_env_for_every_case(monkeypatch, tmp_path, lg, capsys, repo):
    args, out = _replay_args(tmp_path, lg, capsys, repo)

    class Broken:
        def __init__(self, *_):
            raise OSError("no venv")

    monkeypatch.setattr(replay, "RUNNER", replay._DEFAULT_RUNNER)
    monkeypatch.setattr(replay, "RealRunner", Broken)
    assert replay.main(args) == 3
    assert [r["reason"] for r in _rows(out)] == ["VERSION_ENV", "VERSION_ENV"]
    assert replay._ACTIVE_RUNNER is None


def test_the_per_case_state_directory_is_mode_0700(tmp_path, monkeypatch):
    seen = tmp_path / "mode.txt"
    state = '"${1#--state=}"'
    body = f"stat -f %Lp {state} > {seen} 2>/dev/null || stat -c %a {state} > {seen}\nexit 1\n"
    runner = _fake_runner(tmp_path, body)
    _stub_git(monkeypatch)
    with pytest.raises(RunnerFailure):
        runner.run(CASE, NO_ARGS)
    assert seen.read_text().strip() == "700"


def test_replay_parser_rejects_missing_options():
    with pytest.raises(SystemExit):
        replay._parser().parse_args(["replay"])
