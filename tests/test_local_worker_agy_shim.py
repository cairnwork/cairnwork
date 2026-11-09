"""Contract tests for the experimental AgyWorker-to-local_worker adapter."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from conftest import commit_change, git

from cairnwork.journal import Journal
from cairnwork.notary import Notary
from cairnwork.ownership import OwnershipLedger
from cairnwork.pipeline import TaskSpec, run_task
from cairnwork.workers.agy import AgyWorker
from experimental.local_worker import agy_shim

PROMPT = "Fix it.\n- Edit only these files: a.py"
ARGV = [
    "-p",
    PROMPT,
    "--model",
    "served-model",
    "--mode",
    "accept-edits",
    "--sandbox",
    "--print-timeout",
    "600s",
]


class FakeProcess:
    def __init__(self, command, checkout: Path, *, reason="COMPLETED", code=0, edit=True):
        self.returncode = code
        self.output = f"LOCAL_WORKER_EPISODE {reason} ep_test path line_sha256=x\n"
        if checkout.exists() and edit and not (checkout / "a.py").is_symlink():
            (checkout / "a.py").write_text("changed\n")
            (checkout / "b.py").write_text("not allowed\n")

    def communicate(self, timeout=None):
        return self.output, None


def fake_world(monkeypatch, tmp_path: Path, *, reason="COMPLETED", code=0, edit=True):
    runs = tmp_path / "runs"
    monkeypatch.setattr(agy_shim, "RUNS", runs)
    monkeypatch.setattr(agy_shim, "CANARY", tmp_path / "canary.json")
    monkeypatch.setattr(agy_shim, "_prepare_run_root", lambda _root: True)

    def clone(cwd: Path, checkout: Path) -> bool:
        checkout.mkdir(parents=True)
        (checkout / ".git").mkdir()
        (checkout / "a.py").write_text("base\n")
        (checkout / "b.py").write_text("base\n")
        return True

    monkeypatch.setattr(agy_shim, "_copy_checkout", clone)
    monkeypatch.setattr(
        agy_shim.subprocess,
        "Popen",
        lambda command, **kwargs: FakeProcess(
            command,
            Path(command[command.index("--checkout") + 1]),
            reason=reason,
            code=code,
            edit=edit,
        ),
    )
    return runs


def test_exact_agy_argv_and_only_allowed_file_is_copied(tmp_path, monkeypatch, capsys):
    assert agy_shim.parse_args(ARGV) == (PROMPT, "served-model", 600)
    cwd = tmp_path / "attempt"
    cwd.mkdir()
    (cwd / "a.py").write_text("base\n")
    (cwd / "b.py").write_text("base\n")
    runs = fake_world(monkeypatch, tmp_path)
    assert agy_shim.execute(PROMPT, "served-model", 600, cwd) == 0
    assert (cwd / "a.py").read_text() == "changed\n"
    assert (cwd / "b.py").read_text() == "base\n"
    assert not list(runs.iterdir())
    assert "LOCAL_WORKER_EPISODE COMPLETED" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("reason", "code"), [("TURN_LIMIT", 0), ("COMPLETED", 2), ("NO_COMMIT", 0)]
)
def test_noncompleted_episode_never_copies_candidate(tmp_path, monkeypatch, reason, code):
    cwd = tmp_path / "attempt"
    cwd.mkdir()
    (cwd / "a.py").write_text("base\n")
    fake_world(monkeypatch, tmp_path, reason=reason, code=code)
    assert agy_shim.execute(PROMPT, "served-model", 600, cwd) != 0
    assert (cwd / "a.py").read_text() == "base\n"


def test_completed_without_an_edit_is_not_a_candidate(tmp_path, monkeypatch):
    cwd = tmp_path / "attempt"
    cwd.mkdir()
    (cwd / "a.py").write_text("base\n")
    fake_world(monkeypatch, tmp_path, edit=False)
    assert agy_shim.execute(PROMPT, "served-model", 600, cwd) != 0
    assert (cwd / "a.py").read_text() == "base\n"


@pytest.mark.parametrize("args", [ARGV + ["--extra"], ARGV[:-3] + ["--print-timeout", "600s"]])
def test_unsupported_argv_is_refused(args):
    assert agy_shim.parse_args(args) is None


def test_missing_scope_and_symlink_scope_are_refused(tmp_path, monkeypatch):
    assert agy_shim.execute("Fix it", "served-model", 600, tmp_path) == 64
    cwd = tmp_path / "attempt"
    cwd.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("protected\n")
    runs = fake_world(monkeypatch, tmp_path)

    def clone_with_link(_cwd, checkout):
        checkout.mkdir(parents=True)
        (checkout / ".git").mkdir()
        (checkout / "a.py").symlink_to(outside)
        return True

    monkeypatch.setattr(agy_shim, "_copy_checkout", clone_with_link)
    prompt = "Fix it.\n- Edit only these files: a.py"
    assert agy_shim.execute(prompt, "served-model", 600, cwd) != 0
    assert outside.read_text() == "protected\n"
    assert not list(runs.iterdir())


@pytest.mark.sandbox
def test_stub_agy_shim_runs_through_pipeline_and_commits_provenance(repo, tmp_path):
    base = commit_change(
        repo,
        {
            "src/calc.py": "def add(a, b):\n    return a + b + 1\n",
            "tests/conftest.py": (
                "import pathlib, sys\n"
                "sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / 'src'))\n"
            ),
            "tests/test_calc.py": (
                "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"
            ),
        },
    )
    shim = tmp_path / "agy-shim-stub"
    shim.write_text(
        f"#!{sys.executable}\n"
        "from pathlib import Path\n"
        "Path('src/calc.py').write_text('def add(a, b):\\n    return a + b\\n')\n"
        "print('stub local_worker episode')\n"
    )
    shim.chmod(0o755)
    task = TaskSpec(
        "agy-shim-integration",
        "agy-shim-card",
        "fix add",
        "add() is off by one",
        repo,
        base,
        ("src/calc.py",),
        (("pytest", ("python3", "-m", "pytest", "-q", "tests/test_calc.py")),),
    )
    ledger, notary, journal = (
        OwnershipLedger(tmp_path / "ownership.sqlite"),
        Notary(tmp_path / "notary.sqlite"),
        Journal(tmp_path / "journal.sqlite"),
    )
    result = run_task(
        task,
        ledger=ledger,
        notary=notary,
        journal=journal,
        worker=AgyWorker(agy=str(shim), model="served-model"),
        run_root=tmp_path / "runs",
    )
    assert result.status == "COMPLETED" and result.verified, (result.reason, result.logs)
    assert result.candidate_commit is not None
    message = git(repo, "show", "-s", "--format=%B", result.candidate_commit)
    assert "Worker: agy" in message
    assert "Requested-Model: served-model" in message
