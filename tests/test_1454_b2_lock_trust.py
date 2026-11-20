"""#1454 B2: the #1353 gate must refuse a venv that the base uv.lock does not describe.

Offline: dists are built by hand and venvs are copies of the running one; nothing is downloaded.
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from pra1353_seal import UntrustedDependency, verify_site
from test_1039_1353_witnesses import PINS

ROOT = Path(__file__).resolve().parents[1]
GIT_ENV = {"PATH": "/usr/bin:/bin", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
LOCK = b"""version = 1
requires-python = ">=3.11"

[[package]]
name = "demo"
version = "0.1"
source = { editable = "." }
dependencies = [{ name = "pytest" }, { name = "colorama", marker = "sys_platform == 'win32'" }]

[[package]]
name = "pytest"
version = "9.1.1"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "colorama"
version = "0.4.6"
source = { registry = "https://pypi.org/simple" }
"""


def _digest(data: bytes) -> str:
    raw = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=")
    return "sha256=" + raw.decode()


def _dist(site: Path, name: str, version: str, files: dict[str, bytes]) -> Path:
    info = site / f"{name}-{version}.dist-info"
    info.mkdir(parents=True)
    files = {**files, f"{info.name}/METADATA": f"Name: {name}\nVersion: {version}\n".encode()}
    rows = []
    for rel, data in files.items():
        (site / rel).parent.mkdir(parents=True, exist_ok=True)
        (site / rel).write_bytes(data)
        rows.append(f"{rel},{_digest(data)},{len(data)}")
    rows += [f"../../../bin/{name},sha256=x,1", f"{info.name}/RECORD,,"]
    (info / "RECORD").write_text("\n".join(rows) + "\n")
    return info


def _honest(site: Path) -> Path:
    return _dist(
        site, "pytest", "9.1.1", {"pytest/__init__.py": b"def main(a=None):\n    return 1\n"}
    )


def test_honest_hand_built_site_matches_the_lock(tmp_path: Path):
    _honest(tmp_path)
    (tmp_path / "pytest/__pycache__").mkdir()
    (tmp_path / "pytest/__pycache__/x.pyc").write_bytes(b"inert under the child's own prefix")
    verify_site(LOCK, [str(tmp_path), str(tmp_path)])


def _fake_pytest_without_dist(site: Path) -> None:
    (site / "pytest").mkdir()
    (site / "pytest/__init__.py").write_text("def main(a=None):\n    return 0\n")


def _version_drift(site: Path) -> None:
    _dist(site, "pytest", "9.9.9", {"pytest/__init__.py": b"def main(a=None):\n    return 0\n"})


def _record_drift(site: Path) -> None:
    _honest(site)
    (site / "pytest/__init__.py").write_text("def main(a=None):\n    return 0\n")


def _extra_dist(site: Path) -> None:
    _honest(site)
    _dist(site, "evil", "1.0", {"evil.py": b"X = 1\n"})


def _unowned_module(site: Path) -> None:
    _honest(site)
    (site / "conftest_plugin.py").write_text("X = 1\n")


def _symlinked_package(site: Path) -> None:
    _honest(site)
    (site / "linked").symlink_to(site / "pytest")


def _unhashed_module(site: Path) -> None:
    info = _honest(site)
    record = info / "RECORD"
    record.write_text(record.read_text() + "pytest/extra.py,,\n")
    (site / "pytest/extra.py").write_text("X = 1\n")


def _missing_required(site: Path) -> None:
    site.mkdir(exist_ok=True)


@pytest.mark.parametrize(
    "plant",
    [
        _fake_pytest_without_dist,
        _version_drift,
        _record_drift,
        _extra_dist,
        _unowned_module,
        _symlinked_package,
        _unhashed_module,
        _missing_required,
    ],
)
def test_untrusted_site_is_refused(plant, tmp_path: Path):
    plant(tmp_path)
    with pytest.raises(UntrustedDependency):
        verify_site(LOCK, [str(tmp_path)])


def test_unreadable_lock_is_refused(tmp_path: Path):
    _honest(tmp_path)
    with pytest.raises(UntrustedDependency, match="unreadable"):
        verify_site(b"not = [toml", [str(tmp_path)])


def _job(workflow: str, job: str) -> list[str]:
    match = re.search(rf"\n  {job}:\n(.*?)(?=\n  [a-z][a-z0-9-]*:\n|\Z)", workflow, re.S)
    assert match is not None, job
    body = match.group(1).split("    steps:\n", 1)[1]
    return re.split(r"\n(?=      - )", body)


@pytest.mark.parametrize("job", ["test", "test-macos-shard"])
def test_ci_stops_workflow_commands_while_the_gate_runs(job: str):
    """The gate runs PR-controlled code; its output must not become ::/##[ workflow commands."""
    steps = _job((ROOT / ".github/workflows/ci.yml").read_text(), job)
    (step,) = [s for s in steps if "tests/pra1353_gate.py" in s]
    start = step.index('echo "::stop-commands::$token"')
    run = step.index("tests/pra1353_gate.py")
    end = step.index('echo "::$token::"')
    assert "/dev/urandom" in step and start < run < end
    assert 'exit "$rc"' in step.split('echo "::$token::"', 1)[1]
    assert "$token" not in step[run : step.index("\n", run)].replace("|| rc=$?", "")


@pytest.mark.parametrize("job", ["test", "test-macos-shard"])
def test_ci_runs_the_gate_from_the_trusted_venv_before_any_pr_code(job: str):
    steps = _job((ROOT / ".github/workflows/ci.yml").read_text(), job)
    gate = [i for i, step in enumerate(steps) if "tests/pra1353_gate.py" in step]
    assert len(gate) == 1
    step = steps[gate[0]]
    assert 'PRA1353_BASE_LOCK="$RUNNER_TEMP/pra1353-trusted/uv.lock"' in step
    assert '"$RUNNER_TEMP/pra1353-trusted/.venv/bin/python" -I -S -B tests/pra1353_gate.py' in step
    build = [s for s in steps[: gate[0]] if "pra1353-trusted" in s]
    assert len(build) == 1
    for needle in (
        "github.event.pull_request.base.sha || github.sha",
        '[[ "$BASE_SHA" =~ ^[0-9a-f]{40}$ ]]',
        'origin -- "$BASE_SHA"',
        "uv sync --no-cache --locked --no-install-project",
        'cd "$trusted"',
        "BASE_REF: ${{ github.base_ref }}",
        "EVENT: ${{ github.event_name }}",
    ):
        assert needle in build[0], needle
    for earlier in steps[: gate[0]]:
        if earlier is build[0]:
            continue
        lines = [ln.strip() for ln in earlier.splitlines() if not ln.strip().startswith("#")]
        assert not any("uv " in line or "scripts/" in line or "sudo" in line for line in lines)


def _venv_copy(tmp: Path, tamper: bool) -> Path:
    venv = tmp / "venv"
    shutil.copytree(sys.prefix, venv, symlinks=True)
    site = next((venv / "lib").glob("python3.*/site-packages"))
    if tamper:
        shutil.rmtree(site / "pytest")
        (site / "pytest").mkdir()
        (site / "pytest/__init__.py").write_text("def main(args=None):\n    return 0\n")
    return venv / "bin/python"


def _gate_repo(root: Path, passing: bool) -> None:
    for name in [*PINS, "tests/pra1353_gate.py"]:
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, root / name)
    shutil.copytree(ROOT / "src", root / "src", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(ROOT / "uv.lock", root / "uv.lock")
    pins = {k: v for k, v in PINS.items() if k != "tests/test_1039_1353_witnesses.py"}
    body = "assert True" if passing else "assert False, 'really failing witness'"
    witness = f"PINS = {pins!r}\ndef test_stub_witness():\n    {body}\n"
    (root / "tests/test_1039_1353_witnesses.py").write_text(witness)
    git = ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t.invalid"]
    subprocess.run(["git", "init", "-q", str(root)], check=True, env=GIT_ENV)
    subprocess.run([*git, "add", "-A"], check=True, env=GIT_ENV)
    subprocess.run([*git, "commit", "-qm", "fixture"], check=True, env=GIT_ENV)


def _gate(python: Path, root: Path) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PRA1353_BASE_LOCK": str(ROOT / "uv.lock")}
    command = [str(python), "-I", "-S", "-B", "tests/pra1353_gate.py"]
    return subprocess.run(
        command, cwd=root, env=env, capture_output=True, text=True, timeout=600, check=False
    )


@pytest.mark.parametrize(("tamper", "passing", "rc"), [(True, False, 3), (False, True, 0)])
def test_gate_end_to_end_fake_pytest_vs_honest_venv(tamper, passing, rc, tmp_path: Path):
    root = tmp_path / "repo"
    _gate_repo(root, passing)
    result = _gate(_venv_copy(tmp_path, tamper), root)
    assert result.returncode == rc, result.stderr[-2000:]
    assert ("UNTRUSTED_DEPENDENCY" in result.stderr) is tamper


def test_ci_mode_without_a_base_lock_fails_closed(tmp_path: Path):
    root = tmp_path / "repo"
    _gate_repo(root, True)
    env = {k: v for k, v in os.environ.items() if k != "PRA1353_BASE_LOCK"}
    command = [sys.executable, "-I", "-S", "-B", "tests/pra1353_gate.py"]
    result = subprocess.run(
        command,
        cwd=root,
        env={**env, "GITHUB_ACTIONS": "true"},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode != 0
    assert "UNTRUSTED_DEPENDENCY" in result.stderr


@pytest.mark.parametrize(
    ("event", "base_ref", "ref", "rc"),
    [
        ("pull_request", "main", "refs/pull/1/merge", 0),
        ("push", "", "refs/heads/main", 0),
        ("pull_request", "attacker-branch", "refs/pull/1/merge", 1),
        ("pull_request", "", "refs/pull/1/merge", 1),
        ("push", "", "refs/heads/other", 1),
        ("workflow_dispatch", "main", "refs/heads/main", 1),
    ],
)
def test_trusted_venv_step_refuses_a_base_other_than_main(event, base_ref, ref, rc):
    steps = _job((ROOT / ".github/workflows/ci.yml").read_text(), "test")
    build = next(s for s in steps if "pra1353-trusted" in s and "uv sync" in s)
    script = textwrap.dedent(build.split("run: |\n", 1)[1])
    guard = script.split('[[ "$BASE_SHA"', 1)[0]
    env = {"EVENT": event, "BASE_REF": base_ref, "GITHUB_REF": ref, "PATH": "/usr/bin:/bin"}
    done = subprocess.run(
        ["bash", "-c", guard], env=env, capture_output=True, text=True, timeout=30
    )
    assert done.returncode == rc, done.stdout + done.stderr


def test_verify_site_does_not_claim_a_byte_comparison():
    """#1487: the check trusts each dist's own RECORD, and its docstring must say so."""
    doc = " ".join((verify_site.__doc__ or "").split())
    assert "byte for byte" not in doc
    assert "RECORD" in doc
    assert "wheel hashes" in doc
    assert "does not compare file bytes" in doc
