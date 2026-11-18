"""Step 3b verifier and state-config binding regressions."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from experimental.acceptance_casebook import replay, replay_inputs
from experimental.acceptance_casebook.replay_runner import RealRunner
from experimental.acceptance_casebook.store import CasebookError

CASE = {"line_sha256": "a" * 64, "base": "b" * 40, "candidate": "c" * 40}


def _bound(ids: list[str], commands: list[Any], binding=None) -> dict:
    request = {
        "acceptance_contract": {"allowed_paths": ["x.py"]},
        "verification_plan": {"required_verifier_ids": ids},
    }
    return replay_inputs.bound_inputs(
        CASE,
        {CASE["line_sha256"]: {"allowed_file": ["x.py"], "verifier": commands}},
        request,
        binding,
    )


def test_commands_bind_to_derived_pytest_ids():
    assert _bound(["pytest-0", "pytest-1"], ["pytest -q", "python -m pytest"])["verifier"] == [
        "pytest -q",
        "python -m pytest",
    ]


def test_id_order_is_irrelevant():
    assert _bound(["pytest-1", "pytest-0"], ["pytest -q", "python -m pytest"])


@pytest.mark.parametrize(
    "ids,commands",
    [
        (["pytest-0", "p2p-direct"], ["pytest -q"]),
        (["pytest-0", "pytest-1"], ["pytest -q"]),
        (["pytest"], ["pytest -q"]),
        (["pytest-0", "pytest-0"], ["pytest -q", "python -m pytest"]),
        (["pytest-0"], ["pytest -q", "python -m pytest"]),
        (["pytest-0", "pytest-1"], ["pytest -q", "pytest -q"]),
    ],
)
def test_bad_verifier_id_mapping_is_rejected(ids, commands):
    with pytest.raises(CasebookError, match="REPLAY_INPUT_MISMATCH"):
        _bound(ids, commands)


def test_verifier_command_values_must_be_strings():
    with pytest.raises(CasebookError, match="REPLAY_INPUT_MISMATCH"):
        _bound(["pytest-0"], [3])


def test_recorded_binding_commands_must_match_tokens():
    binding = {"verifiers": [["pytest-0", ["python3", "-m", "pytest", "-q"]]]}
    assert _bound(["pytest-0"], ["python3 -m pytest -q"], binding)
    with pytest.raises(CasebookError, match="REPLAY_INPUT_MISMATCH"):
        _bound(["pytest-0"], ["pytest -q"], binding)


def test_template_copy_is_private_and_output_omits_contents(tmp_path: Path, monkeypatch):
    marker = "token-file-name-that-must-not-be-output"
    template = tmp_path / "config.json"
    template.write_text(json.dumps({"core_token_file": marker}))
    runner = RealRunner.__new__(RealRunner)
    runner.root, runner.repo, runner.timeout = tmp_path / "root", tmp_path / "repo", 1
    runner.root.mkdir()
    runner.repo.mkdir()
    state = runner.root / "case-state"
    state.mkdir(mode=0o700)
    runner._copy_state_config(template, state)
    copied = state / "config.json"
    assert copied.read_text() == template.read_text()
    assert copied.stat().st_mode & 0o777 == 0o600
    assert state.stat().st_mode & 0o777 == 0o700
    assert copied.parent == state
    monkeypatch.setattr(
        replay,
        "_ACTIVE_RUNNER",
        lambda *_: {"exit_code": 0, "verdict": "accepted", "output": "NO_CORE_VERDICT"},
    )
    record = replay._case_result(
        {**CASE, "kind": "accepted_bad", "origin": {"row_sha256": "j" * 64}},
        {},
        {},
        "d" * 64,
        "e" * 40,
        "f" * 40,
        "g" * 64,
        "h" * 64,
        {},
        1,
        "i" * 64,
    )
    output_record = json.dumps(record)
    assert marker not in output_record
    assert template.exists()


@pytest.mark.parametrize("kind", ["symlink", "missing", "directory"])
def test_bad_state_config_template_is_refused(tmp_path: Path, kind: str):
    template = tmp_path / "template.json"
    if kind == "symlink":
        real = tmp_path / "real.json"
        real.write_text("{}")
        template.symlink_to(real)
    elif kind == "directory":
        template.mkdir()
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    with pytest.raises(CasebookError, match="REPLAY_INPUT_INVALID"):
        RealRunner._copy_state_config(template, state)


def test_runner_run_copies_template_privately_and_never_returns_it(tmp_path: Path, monkeypatch):
    import subprocess

    import experimental.acceptance_casebook.replay_runner as runner_module

    marker = "token-file-name-that-must-not-be-output"
    template = tmp_path / "template.json"
    template.write_text(json.dumps({"core_token_file": marker}))
    seen = tmp_path / "seen.txt"
    state = '"$S"'
    verdict = '{"status":"VERIFIED","verified":true,"core_status":"VERIFIED"}'
    runner = RealRunner.__new__(RealRunner)
    runner.root, runner.repo, runner.timeout = tmp_path, tmp_path / "repo", 5
    runner.state_config = template
    runner.repo.mkdir()
    (tmp_path / "venv" / "bin").mkdir(parents=True)
    runner.venv = tmp_path / "venv"
    body = (
        "#!/bin/sh\n"
        "for a; do case $a in --state=*) S=${a#--state=};; esac; done\n"
        f"(stat -c %a {state}/config.json 2>/dev/null"
        f" || stat -f %Lp {state}/config.json) > {seen}\n"
        f"cat {state}/config.json >> {seen}\n"
        f"printf '%s' '{verdict}'\n"
    )
    for name in ("cairnwork-verify", "python"):
        script = tmp_path / "venv" / "bin" / name
        script.write_text(body)
        script.chmod(0o755)
    original = subprocess.run

    def run(argv, **kwargs):
        if argv[0] == "git":
            if "clone" in argv:
                Path(argv[-1]).mkdir()
            return subprocess.CompletedProcess(argv, 0)
        return original(argv, **kwargs)

    monkeypatch.setattr(runner_module.subprocess, "run", run)
    result = runner.run(
        {"base": "a" * 40, "candidate": "b" * 40}, {"allowed_file": [], "verifier": []}
    )
    assert seen.read_text().splitlines()[0] == "600"
    assert marker in seen.read_text()
    assert result.output == verdict and marker not in json.dumps(result.output)
    assert not [p for p in tmp_path.glob("case-*")]
    monkeypatch.setattr(replay, "_ACTIVE_RUNNER", runner)
    record = replay._case_result(
        {**CASE, "kind": "accepted_bad", "origin": {"row_sha256": "j" * 64}},
        {"allowed_file": [], "verifier": []},
        {},
        "d" * 64,
        "e" * 40,
        "f" * 40,
        "g" * 64,
        "h" * 64,
        {},
        1,
        "i" * 64,
    )
    assert marker not in json.dumps(record)
    assert record["verify_output_sha256"] == [replay._digest(verdict.encode())]


@pytest.mark.parametrize(
    "verdict,code,mapped,reason",
    [
        ("accepted", 0, "accepted", ""),
        ("refused", 2, "refused", ""),
        ("accepted", 2, "UNREPLAYABLE", "UNREADABLE_OUTPUT"),
        ("refused", 0, "UNREPLAYABLE", "UNREADABLE_OUTPUT"),
        ("FAILED", 0, "UNREPLAYABLE", "UNREADABLE_OUTPUT"),
        ("VERIFIED", 0, "UNREPLAYABLE", "UNREADABLE_OUTPUT"),
        ("accepted", True, "UNREPLAYABLE", "UNREADABLE_OUTPUT"),
        ("accepted", False, "UNREPLAYABLE", "UNREADABLE_OUTPUT"),
    ],
)
def test_only_a_checked_core_verdict_maps_to_a_verdict(monkeypatch, verdict, code, mapped, reason):
    from experimental.acceptance_casebook.replay_runner import CoreVerdict

    monkeypatch.setattr(replay, "RUNNER", lambda *_: CoreVerdict(verdict, code, "x"))
    got, why, _, _ = replay._run_one({"kind": "accepted_bad"}, {}, 0)
    assert (got, why) == (mapped, reason)


def test_runner_installs_from_the_archived_lock_not_by_offline_resolution(
    tmp_path: Path, monkeypatch
):
    """CI fills the uv cache with `uv sync --locked`: locked wheels, no registry index pages.

    An offline `uv pip install -e` re-resolves dependencies ("cryptography was not found in the
    cache"), so the runner must install from the archived uv.lock into the per-run venv instead.
    """
    import subprocess

    import experimental.acceptance_casebook.replay_runner as runner_module

    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "shadow"))
    monkeypatch.setenv("VIRTUAL_ENV", str(tmp_path / "outer"))
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def run(argv, **kwargs):
        calls.append((list(argv), kwargs))
        if argv[0] == "tar":
            Path(argv[-1], "marker").write_text("x")
        out = ""
        if "-I" in argv:
            out = "unused"
        return subprocess.CompletedProcess(argv, 0, stdout=out, stderr="")

    monkeypatch.setattr(runner_module.subprocess, "run", run)
    monkeypatch.setattr(runner_module, "assert_copy_import", lambda *_: None)
    runner = RealRunner(tmp_path, tmp_path, "a" * 40, 5)
    try:
        flat = [" ".join(argv) for argv, _ in calls]
        assert not [c for c in flat if c.startswith("uv pip")]
        (sync,) = [(a, k) for a, k in calls if a[:2] == ["uv", "sync"]]
        assert {"--offline", "--locked"} <= set(sync[0])
        assert sync[0][sync[0].index("--project") + 1] == str(runner.copy)
        env = sync[1]["env"]
        assert env["UV_PROJECT_ENVIRONMENT"] == str(runner.venv)
        assert "PYTHONPATH" not in env and "VIRTUAL_ENV" not in env
    finally:
        runner.close()
