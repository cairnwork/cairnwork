"""Follow-up tests for the local_relay hidden-tests fixture support (#1533)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from experimental.local_relay import runtime
from experimental.local_relay.hidden_tests import HiddenVerify, build_bundle, load_hidden
from experimental.pause.core_judge import CoreJudge


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@e", *args],
        cwd=cwd, capture_output=True, text=True, check=True,
    ).stdout.strip()  # fmt: skip


def _repo(tmp_path: Path, *, fixture: dict[str, Any] | None = None) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_public.py").write_text("def test_p():\n    pass\n")
    if fixture is not None:
        (repo / runtime.FIXTURE_TASK).write_text(json.dumps(fixture))
    _git(tmp_path, "init", "-q", str(repo))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    return repo, _git(repo, "rev-parse", "HEAD")


def _private_dir(path: Path) -> Path:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.chmod(0o700)
    (path / "test_hidden.py").write_text("def test_h():\n    assert False\n")
    return path


def test_private_hidden_dir_inside_the_repo_is_refused(tmp_path):
    repo, _base = _repo(tmp_path)
    hidden = _private_dir(repo / "private_tests")  # 0700: only the location check can refuse it
    assert hidden.lstat().st_mode & 0o077 == 0
    with pytest.raises(ValueError, match="outside the fixture repository"):
        load_hidden({"hidden_tests_dir": str(hidden)}, repo)


def test_private_hidden_dir_above_the_repo_is_refused(tmp_path):
    outer = tmp_path / "outer"
    outer.mkdir()
    repo, _base = _repo(outer)
    outer.chmod(0o700)
    (outer / "test_hidden.py").write_text("def test_h():\n    assert False\n")
    assert outer.lstat().st_mode & 0o077 == 0 and repo.parent == outer
    with pytest.raises(ValueError, match="outside the fixture repository"):
        load_hidden({"hidden_tests_dir": str(outer)}, repo)


def test_build_run_once_wires_hidden_verify_and_verifier_python(tmp_path, monkeypatch):
    hidden_dir = _private_dir(tmp_path / "private")
    fixture = {
        "task_id": "c" * 32, "prompt": "fix add", "allowed_files": ["src/calc.py"],
        "verifiers": [["python3", "-m", "pytest", "-q", "tests/test_hidden.py"]],
        "hidden_tests_dir": str(hidden_dir), "verifier_python": sys.executable,
    }  # fmt: skip
    repo, _base = _repo(tmp_path, fixture=fixture)
    judges: list[CoreJudge] = []

    class Recording(CoreJudge):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            judges.append(self)

    monkeypatch.setattr(runtime, "CoreJudge", Recording)
    args = SimpleNamespace(fixture=repo, state=str(tmp_path / "state"), canary_report=None)
    assert callable(runtime.build_run_once(args))
    (judge,) = judges
    assert isinstance(judge.verify, HiddenVerify)  # the judge calls the hidden-commit wrapper
    assert judge.verify.hidden.files == ("tests/test_hidden.py",)
    assert judge.intake.config()["verifier_python"] == sys.executable


def test_commits_built_in_the_same_second_are_all_distinct(tmp_path):
    repo, base = _repo(tmp_path)
    hidden = load_hidden({"hidden_tests_dir": str(_private_dir(tmp_path / "private"))}, repo)
    assert hidden is not None
    shas, times = [], []
    for i in range(3):
        out = tmp_path / f"out{i}"
        out.mkdir()
        shas.append(build_bundle(repo, base, hidden, out))
        times.append(_git(out / "work", "show", "-s", "--format=%ct", shas[-1]))
    assert len(set(shas)) == 3  # a repeated sha would be a used test commit the store refuses
    assert len(set(times)) < 3  # at least two were built in the same second
