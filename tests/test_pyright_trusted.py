from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.ci import pyright_trusted_status as status

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "ci" / "pyright_trusted.sh"
WORKFLOW = (ROOT / ".github" / "workflows" / "pyright-trusted.yml").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("job", "rc", "state"),
    [
        ("success", "0", "success"),
        ("success", "1", "failure"),
        ("success", "2", "error"),
        ("success", "3", "error"),
        ("success", "", "error"),
        ("success", "garbage", "error"),
        ("failure", "0", "error"),
        ("cancelled", "0", "error"),
        ("skipped", "", "error"),
        ("", "0", "error"),
    ],
)
def test_only_clean_success_passes(job: str, rc: str, state: str) -> None:
    assert status.decide(job, rc) == state


def test_description_is_fixed_text(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    out = tmp_path / "out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.setenv("PYRIGHT_JOB_RESULT", "success\nstate=success")
    monkeypatch.setenv("PYRIGHT_GATE_RC", "0")
    status.main()
    lines = out.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "state=error"
    assert lines[1] == f"description={status.DESCRIPTIONS['error']}"


def test_workflow_trust_boundary() -> None:
    assert "pull_request_target:" in WORKFLOW
    assert WORKFLOW.count("persist-credentials: false") == 3
    assert "enable-cache: false" in WORKFLOW
    assert "edited" in WORKFLOW
    # Only the publish job may write statuses; the job that reads PR content may not.
    analyze, publish = WORKFLOW.split("  publish:")
    assert "statuses: write" not in analyze
    assert "statuses: write" in publish
    assert "head.sha" in analyze
    assert "uv sync" not in WORKFLOW  # dependency install happens inside the trusted script
    assert "run: python3 scripts/ci/pyright_trusted_status.py" in publish
    assert "${{ github.event.pull_request.title" not in WORKFLOW


def _git(cwd: Path, *args: str) -> str:
    env = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
        "PATH": os.environ["PATH"],
        "HOME": str(cwd),
    }
    done = subprocess.run(
        ["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True
    )
    return done.stdout.strip()


def test_fork_head_without_base_commit_is_fetched(tmp_path: Path) -> None:
    base = tmp_path / "base"
    base.mkdir()
    _git(base, "init", "-q", "-b", "main")
    (base / "a").write_text("1")
    _git(base, "add", "a")
    _git(base, "commit", "-qm", "c1")
    c1 = _git(base, "rev-parse", "HEAD")
    (base / "a").write_text("2")
    _git(base, "commit", "-qam", "c2")
    c2 = _git(base, "rev-parse", "HEAD")
    head = tmp_path / "head"
    head.mkdir()
    _git(head, "init", "-q", "-b", "pr")
    _git(head, "fetch", "-q", str(base), c1)
    _git(head, "checkout", "-q", c1)
    (head / "b").write_text("x")
    _git(head, "add", "b")
    _git(head, "commit", "-qm", "h")
    assert subprocess.run(["git", "cat-file", "-e", c2], cwd=head).returncode != 0
    fake = tmp_path / "bin"
    fake.mkdir()
    for name, body in (("uv", "exit 0"), ("uvx", "exit 9")):
        (fake / name).write_text(f"#!/bin/sh\n{body}\n")
        (fake / name).chmod(0o755)
    trusted = tmp_path / "trusted"
    trusted.mkdir()
    env = {**os.environ, "PATH": f"{fake}:{os.environ['PATH']}", "BASE_REPO_URL": str(base)}
    done = subprocess.run(
        [str(SCRIPT), str(trusted), str(head), c2, str(tmp_path / "work")],
        env=env,
        capture_output=True,
        text=True,
    )
    assert f"merge base: {c1}" in done.stdout  # fetched, merge base found
    assert "cannot fetch" not in done.stdout
    assert done.returncode == 3  # fake pyright crashes afterwards


def _setup_trusted_run(tmp_path: Path) -> tuple[Path, Path, Path, str, Path, Path]:
    base = tmp_path / "base"
    base.mkdir()
    _git(base, "init", "-q", "-b", "main")
    (base / "m.py").write_text("x = 1\n", encoding="utf-8")
    _git(base, "add", "m.py")
    _git(base, "commit", "-qm", "base")
    c1 = _git(base, "rev-parse", "HEAD")

    head = tmp_path / "head"
    subprocess.run(["git", "clone", "-q", str(base), str(head)], check=True)
    trusted = tmp_path / "trusted"
    (trusted / "scripts").mkdir(parents=True)
    shutil.copy2(ROOT / "scripts" / "pyright_gate.py", trusted / "scripts" / "pyright_gate.py")
    (trusted / "pyrightconfig.json").write_text('{"typeCheckingMode": "strict"}', encoding="utf-8")
    (trusted / ".venv" / "bin").mkdir(parents=True)
    os.symlink(sys.executable, trusted / ".venv" / "bin" / "python")

    seen = tmp_path / "seen.txt"
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "uv").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (fake / "uv").chmod(0o755)
    uvx_body = (
        "#!/bin/sh\n"
        'proj=""\n'
        "while [ $# -gt 0 ]; do\n"
        '  if [ "$1" = "--project" ]; then proj="$2"; shift 2; else shift; fi\n'
        "done\n"
        'printf \'%s\\t%s\\n\' "$proj" "$(cat "$proj/pyrightconfig.json" 2>/dev/null)"'
        ' >> "$SEEN_FILE"\n'
        'printf \'{"generalDiagnostics": [], "summary": {"filesAnalyzed": 1}}\\n\'\n'
    )
    (fake / "uvx").write_text(uvx_body, encoding="utf-8")
    (fake / "uvx").chmod(0o755)
    return trusted, head, fake, c1, seen, tmp_path / "work"


def test_trusted_script_enforces_trusted_config_and_rejects_suppressions(
    tmp_path: Path,
) -> None:
    trusted, head, fake, base_sha, seen, work = _setup_trusted_run(tmp_path)
    env = {**os.environ, "PATH": f"{fake}:{os.environ['PATH']}", "SEEN_FILE": str(seen)}

    (head / "m.py").write_text("# type: ignore\nx = 1\n", encoding="utf-8")
    _git(head, "commit", "-qam", "add file ignore")
    done_ignore = subprocess.run(
        [str(SCRIPT), str(trusted), str(head), base_sha, str(work / "w1")],
        env=env,
        capture_output=True,
        text=True,
    )
    assert done_ignore.returncode == 2
    assert status.decide("success", str(done_ignore.returncode)) == "error"
    assert not seen.exists()

    (head / "m.py").write_text("x = 2\n", encoding="utf-8")
    (head / "m.pyi").write_text("x: int\n", encoding="utf-8")
    _git(head, "add", "m.py", "m.pyi")
    _git(head, "commit", "-qm", "add stub")
    done_stub = subprocess.run(
        [str(SCRIPT), str(trusted), str(head), base_sha, str(work / "w2")],
        env=env,
        capture_output=True,
        text=True,
    )
    assert done_stub.returncode == 2
    assert not seen.exists()

    _git(head, "rm", "-q", "m.pyi")
    _git(head, "commit", "-qm", "clean change")
    done_clean = subprocess.run(
        [str(SCRIPT), str(trusted), str(head), base_sha, str(work / "w3")],
        env=env,
        capture_output=True,
        text=True,
    )
    assert done_clean.returncode == 0
    assert status.decide("success", str(done_clean.returncode)) == "success"
    records = [line.split("\t") for line in seen.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 2
    assert all(json.loads(cfg) == {"typeCheckingMode": "strict"} for _, cfg in records)


def test_trusted_script_fails_closed_when_trusted_python_missing(tmp_path: Path) -> None:
    trusted, head, fake, base_sha, seen, work = _setup_trusted_run(tmp_path)
    (trusted / ".venv" / "bin" / "python").unlink()
    env = {**os.environ, "PATH": f"{fake}:{os.environ['PATH']}", "SEEN_FILE": str(seen)}
    done = subprocess.run(
        [str(SCRIPT), str(trusted), str(head), base_sha, str(work)],
        env=env,
        capture_output=True,
        text=True,
    )
    assert done.returncode == 3
    assert "trusted python missing" in done.stdout
    assert not seen.exists()
