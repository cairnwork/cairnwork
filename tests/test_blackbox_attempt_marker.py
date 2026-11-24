"""#543: the git commands a blackbox run starts carry the attempt marker, so restart recovery
can find and stop an orphaned one after a crash (as tests/test_gitcmd_marker.py does for the
verifier path)."""

import json
import os
import subprocess

from conftest import commit_change

from cairnwork import blackbox, blackbox_exec
from cairnwork.confined import ATTEMPT_ENV
from cairnwork.verify_context import VerifyContext

SALT = "ab" * 32
CHECK = {"id": "n", "module": "calc", "function": "one",
         "cases": [{"id": "c1", "args": [], "expected": 1}]}  # fmt: skip


def _git_markers(repo, tmp_path, monkeypatch, attempt_id):
    seen = []
    inside = []  # True while blackbox_exec._run is on the stack: the marked block lives there
    real_run, real_inner = subprocess.run, blackbox_exec._run

    def spy_inner(*args, **kwargs):
        inside.append(True)
        try:
            return real_inner(*args, **kwargs)
        finally:
            inside.pop()

    def spy(*args, **kwargs):
        cmd = args[0] if args else kwargs.get("args", [])
        if (
            inside and cmd and os.path.basename(str(cmd[0])) == "git" and "version" not in cmd
        ):  # the cached capability probe is not a run command
            seen.append((kwargs.get("env") or {}).get(ATTEMPT_ENV))
        return real_run(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", spy)
    monkeypatch.setattr(blackbox_exec, "_run", spy_inner)
    declared = blackbox.parse(json.dumps({"salt": SALT, "checks": [CHECK]}).encode())
    head = commit_change(repo, {"calc.py": "def one():\n    return 1\n"})
    ctx = VerifyContext(repo, tmp_path / "runs", attempt_id=attempt_id)
    blackbox_exec.run_blackbox(declared, ctx, head, "a1-v")
    return seen


def test_every_git_command_of_a_blackbox_run_carries_the_attempt_marker(
    repo, tmp_path, monkeypatch
):
    seen = _git_markers(repo, tmp_path, monkeypatch, "att543")
    assert seen, "the run must start git (checkout)"
    assert all(marker == "att543" for marker in seen), seen


def test_no_attempt_id_sets_no_marker(repo, tmp_path, monkeypatch):
    seen = _git_markers(repo, tmp_path, monkeypatch, "")
    assert seen
    assert all(marker is None for marker in seen), seen
