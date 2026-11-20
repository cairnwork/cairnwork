"""Issue #751 step 3b AC4f: the real runner drives the real cairnwork-verify under Seatbelt."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path
from typing import Any

import pytest
from attest.test_attest_cli import CORE_IDENTITY, fake_core_verify
from core_http_server import core_http_server
from test_acceptance_casebook_replay import _IDENT, _args, _confirmed
from verify_helpers import FIX, VERIFY, base_of, candidate

from cairnwork.core_request import AcceptanceSpec, VerifierObservation
from cairnwork.notary import Notary
from experimental.acceptance_casebook import replay

pytest_plugins = ["test_acceptance_casebook_origin"]


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def _version_clone(tmp_path: Path) -> tuple[Path, str]:
    """A repository whose single commit is the archived checkout of this worktree's HEAD."""
    clone = tmp_path / "version-clone"
    clone.mkdir()
    subprocess.run(["git", "init", "-q", str(clone)], check=True)
    archive = subprocess.run(
        ["git", "-C", str(Path.cwd()), "archive", "HEAD"], check=True, capture_output=True
    ).stdout
    (tmp_path / "worktree.tar").write_bytes(archive)
    with tarfile.open(tmp_path / "worktree.tar") as bundle:
        bundle.extractall(clone, filter="data")
    subprocess.run(["git", "-C", str(clone), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(clone), "commit", "-qm", "archived version"],
        check=True,
        env={**os.environ, **_IDENT},
    )
    return clone, _git(clone, "rev-parse", "HEAD")


def _failing_core(request: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    status, response = fake_core_verify(request)
    response["verification"] = {"status": "FAILED", "integrity": "VALID", "reason_codes": ["X"]}
    return status, response


def _origin(tmp_path: Path, project: Path) -> dict[str, str]:
    """Origin rows whose verifier id is the verifier command, as replay binds the two."""
    db = tmp_path / "origin" / "notary.sqlite"
    db.parent.mkdir()
    base = base_of(project)
    good = candidate(project, base, {"src/calc.py": FIX})
    bad = candidate(project, base, {"src/calc.py": "def add(a, b):\n    return a - b\n"})
    spec = AcceptanceSpec("task-1", "sha256:" + "c" * 64, ("pytest-0",), ("src/calc.py",), False)
    obs = (VerifierObservation("pytest-0", "artifact-1", "sha256:" + "e" * 64, True),)
    for attempt, cand, core in (("a1", good, fake_core_verify), ("a2", bad, _failing_core)):
        notary = Notary(db, core_verify=core, core_identity=CORE_IDENTITY)
        notary.notarize("task-1", attempt, spec, project, base, cand, obs)
    return {"db": str(db), "base": base, "cand": good, "refused": bad, "dir": str(tmp_path)}


@pytest.mark.sandbox
def test_ac4f_replays_through_real_runner_and_verify_cli(tmp_path, capsys, project):
    lg = _origin(tmp_path, project)
    cases = [
        _confirmed(lg, capsys, "a1", "accepted_bad"),
        _confirmed(lg, capsys, "a2", "refused_good"),
    ]
    args = _args(tmp_path, lg, cases)
    clone, version = _version_clone(tmp_path)
    registry = Path(args[args.index("--versions") + 1])
    registry.write_text(json.dumps({version: {"tree": _git(clone, "rev-parse", "HEAD^{tree}")}}))
    args[args.index("--version") + 1] = version
    args[args.index("--cairnwork-clone") + 1] = str(clone)
    args += ["--repo", str(project), "--repeat", "1"]
    with core_http_server(tmp_path) as (url, token):
        state_config = tmp_path / "state-config.json"
        state_config.write_text(
            json.dumps(
                {
                    "verifier_account": False,
                    "verifier_python": sys.executable,
                    "core_url": url,
                    "core_token_file": str(token),
                }
            )
        )
        args += ["--state-config", str(state_config)]
        inputs = Path(args[args.index("--inputs") + 1])
        data = json.loads(inputs.read_text())
        for row in data.values():
            row["verifier"] = [VERIFY]
        inputs.write_text(json.dumps(data))
        code = replay.main(args)
    lines = Path(args[args.index("--out") + 1]).read_text().splitlines()
    first, second = (json.loads(line) for line in lines[:2])
    assert first["outcome"] == "same_mistake" and not first.get("reason")
    assert first["mapped"] == ["accepted"] and first["exit_codes"] == [0]
    assert first["verify_output_sha256"][0]
    # A failing verifier reaches Core, whose real FAILED_VERIFICATION is a refusal (#751).
    assert second["outcome"] == "same_mistake" and not second.get("reason")
    assert second["mapped"] == ["refused"] and second["exit_codes"] == [2]
    assert code == 0  # both replays reached a Core verdict
