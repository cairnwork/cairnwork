"""Issue #751 step 3b: the verifier runs in the checked interpreter with one scrubbed env."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from core_http_server import core_http_server
from test_acceptance_casebook_replay import _args, _confirmed
from test_acceptance_casebook_replay_runner_e2e import _git, _origin, _version_clone
from verify_helpers import VERIFY

import experimental.acceptance_casebook.replay_runner as runner_module
from experimental.acceptance_casebook import replay
from experimental.acceptance_casebook.replay_runner import RealRunner
from experimental.acceptance_casebook.store import CasebookError

pytest_plugins = ["test_acceptance_casebook_origin"]

LEAKY = ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PYTHONUSERBASE", "VIRTUAL_ENV")
FORGED = '{"status":"VERIFIED","verified":true,"core_status":"VERIFIED"}'


def _dirty(monkeypatch) -> None:
    for key in LEAKY:
        monkeypatch.setenv(key, "/nonexistent/" + key)
    monkeypatch.setenv("__PYVENV_LAUNCHER__", "/nonexistent/launcher")
    monkeypatch.setenv("PYTHONNOUSERSITE", "0")


def test_check_and_verifier_share_one_scrubbed_env_and_isolated_argv(tmp_path, monkeypatch):
    _dirty(monkeypatch)
    envs: list[Any] = []
    argvs: list[list[str]] = []

    def run(argv, **kwargs):
        if argv[0] == "git" and "archive" in argv:
            kwargs["stdout"].write(b"")
        if "-c" in argv:
            envs.append(kwargs.get("env"))
            return subprocess.CompletedProcess(argv, 0, stdout=str(tmp_path / "x"), stderr="")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    class Popen:
        def __init__(self, argv, **kwargs):
            envs.append(kwargs.get("env"))
            argvs.append(argv)
            self.pid, self.returncode = 0, 0

        def communicate(self, timeout=None):
            return FORGED, ""

    monkeypatch.setattr(runner_module.subprocess, "run", run)
    monkeypatch.setattr(runner_module.subprocess, "Popen", Popen)
    monkeypatch.setattr(runner_module, "assert_copy_import", lambda *_: None)
    monkeypatch.setattr(runner_module.tempfile, "mkdtemp", lambda **_: str(tmp_path))
    monkeypatch.setattr(runner_module.shutil, "rmtree", lambda *_a, **_k: None)
    runner = RealRunner(tmp_path, tmp_path, "v", 5)
    runner.state_config = None
    (tmp_path / "repo").mkdir(exist_ok=True)
    runner_module.run_verifier(
        runner_module.build_argv(
            runner.venv,
            tmp_path,
            tmp_path,
            {"base": "a", "candidate": "b"},
            {"allowed_file": [], "verifier": []},
        ),
        5,
    )
    assert len(envs) == 2 and envs[0] == envs[1] and isinstance(envs[0], dict)
    assert not [k for k in envs[0] if k.startswith("PYTHON") and k != "PYTHONNOUSERSITE"]
    assert not {*LEAKY, "__PYVENV_LAUNCHER__"} & set(envs[0])
    assert envs[0]["PYTHONNOUSERSITE"] == "1" and "PATH" in envs[0]
    assert argvs[0][:2] == [str(runner.venv / "bin" / "python"), "-I"]


def test_a_fifo_state_config_template_is_rejected_promptly(tmp_path):
    template = tmp_path / "fifo"
    os.mkfifo(template)
    state = tmp_path / "state"
    state.mkdir()

    def hung(*_):
        raise AssertionError("opening the FIFO blocked")

    signal.signal(signal.SIGALRM, hung)
    signal.alarm(5)
    try:
        with pytest.raises(CasebookError, match="REPLAY_INPUT_INVALID"):
            RealRunner._copy_state_config(template, state)
    finally:
        signal.alarm(0)
    assert not (state / "config.json").exists()


@pytest.mark.sandbox
def test_a_shadow_cairnwork_on_pythonpath_cannot_forge_a_core_verdict(
    tmp_path, capsys, project, monkeypatch
):
    shadow = tmp_path / "shadow" / "cairnwork"
    shadow.mkdir(parents=True)
    (shadow / "__init__.py").write_text("")
    (shadow / "verify_cli.py").write_text(
        f"import sys\ndef main():\n    print('{FORGED}')\n    return 0\n"
        "if __name__ == '__main__':\n    sys.exit(main())\n"
    )
    monkeypatch.setenv("PYTHONPATH", str(shadow.parent))
    lg = _origin(tmp_path, project)
    cases = [_confirmed(lg, capsys, "a2", "refused_good")]
    args = _args(tmp_path, lg, cases)
    clone, version = _version_clone(tmp_path)
    registry = Path(args[args.index("--versions") + 1])
    registry.write_text(json.dumps({version: {"tree": _git(clone, "rev-parse", "HEAD^{tree}")}}))
    args[args.index("--version") + 1] = version
    args[args.index("--cairnwork-clone") + 1] = str(clone)
    args += ["--repo", str(project), "--repeat", "1"]
    with core_http_server(tmp_path) as (url, token):
        config = tmp_path / "state-config.json"
        config.write_text(
            json.dumps(
                {
                    "verifier_account": False,
                    "verifier_python": sys.executable,
                    "core_url": url,
                    "core_token_file": str(token),
                }
            )
        )
        args += ["--state-config", str(config)]
        inputs = Path(args[args.index("--inputs") + 1])
        data = json.loads(inputs.read_text())
        for row in data.values():
            row["verifier"] = [VERIFY]
        inputs.write_text(json.dumps(data))
        replay.main(args)
    (line,) = Path(args[args.index("--out") + 1]).read_text().splitlines()[:1]
    record = json.loads(line)
    assert record["mapped"] != ["accepted"]
    assert record["outcome"] == "UNREPLAYABLE" and record["reason"] == "NO_CORE_VERDICT"
