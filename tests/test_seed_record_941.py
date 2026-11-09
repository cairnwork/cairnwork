"""Tests pinning the hash-seed replay record (Issue #941, item 2).

Verifies that both pipeline.run_task and verify_candidate create exactly one mode 0600
verifier-seeds/<attempt_id>.json record per attempt, and that the recorded seed is the one
handed to verifiers._prepare_run (which sets the child's PYTHONHASHSEED). The child's own view
is not observed: a sandboxed verifier can neither write outside its run nor leave a log here.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import git
from test_pipeline import EditWorker
from verify_helpers import FIX, VERIFY, base_of, candidate

from cairnwork import verifiers, verify_cli
from cairnwork.journal import Journal
from cairnwork.notary import Notary
from cairnwork.ownership import OwnershipLedger
from cairnwork.pipeline import TaskSpec, run_task

pytestmark = pytest.mark.sandbox

VERIFY_SPEC = (("pytest", ("python3", "-m", "pytest", "-q", "tests/test_calc.py")),)


@pytest.fixture
def prepared_seeds(monkeypatch) -> list[str]:
    """Every hash_seed handed to verifiers._prepare_run during the test."""
    seen: list[str] = []
    original = verifiers._prepare_run

    def spy(*args, **kwargs):
        seen.append(kwargs.get("hash_seed", args[4] if len(args) > 4 else ""))
        return original(*args, **kwargs)

    monkeypatch.setattr(verifiers, "_prepare_run", spy)
    return seen


@pytest.fixture
def stores(tmp_path: Path) -> tuple[OwnershipLedger, Notary, Journal]:
    return (
        OwnershipLedger(tmp_path / "own.sqlite"),
        Notary(tmp_path / "notary.sqlite"),
        Journal(tmp_path / "journal.sqlite"),
    )


def test_pipeline_run_task_records_and_applies_hash_seed(
    project: Path,
    stores: tuple[OwnershipLedger, Notary, Journal],
    tmp_path: Path,
    prepared_seeds: list[str],
) -> None:
    ledger, notary, journal = stores
    run_root = tmp_path / "runs"
    task = TaskSpec(
        "k1",
        "card-k1",
        "fix add",
        "add() is off by one",
        project,
        git(project, "rev-parse", "HEAD"),
        ("src/calc.py",),
        VERIFY_SPEC,
    )
    worker = EditWorker({"src/calc.py": "def add(a, b):\n    return a + b\n"})
    result = run_task(
        task,
        ledger=ledger,
        notary=notary,
        journal=journal,
        worker=worker,
        run_root=run_root,
    )
    assert result.status == "COMPLETED" and result.verified, result.reason

    attempt_id = result.attempt_id
    assert attempt_id

    seeds_dir = run_root.parent / "verifier-seeds"
    assert seeds_dir.is_dir(), f"seeds directory {seeds_dir} does not exist"
    seed_files = list(seeds_dir.glob("*.json"))
    assert len(seed_files) == 1, f"expected exactly 1 seed file, found {seed_files}"

    seed_path = seeds_dir / f"{attempt_id}.json"
    assert seed_path.exists(), f"seed file {seed_path} was not created"
    assert seed_path.stat().st_mode & 0o777 == 0o600

    data = json.loads(seed_path.read_text(encoding="utf-8"))
    assert set(data.keys()) == {"attempt_id", "hash_seed"}
    assert data["attempt_id"] == attempt_id

    recorded_seed = data["hash_seed"]
    assert recorded_seed.isdecimal() and 0 <= int(recorded_seed) <= 4294967295

    assert prepared_seeds, "verifiers._prepare_run was never called"
    assert set(prepared_seeds) == {recorded_seed}


def test_verify_candidate_records_and_applies_hash_seed(
    project: Path, state: Path, prepared_seeds: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    base = base_of(project)
    cand = candidate(project, base, {"src/calc.py": FIX})

    # In-process (not tests/verify_helpers.verify, which spawns the CLI) so the spy sees the call.
    code = verify_cli.main([
        "--state", str(state), "run", "--repo-path", str(project), "--base", base,
        "--candidate", cand, "--allowed-file", "src/calc.py", "--verifier", VERIFY,
    ])  # fmt: skip
    out = json.loads(capsys.readouterr().out)
    assert code == 0, out
    assert out.get("verified") is True

    attempt_id = out["attempt_id"]
    assert attempt_id

    seeds_dir = state / "verifier-seeds"
    assert seeds_dir.is_dir(), f"seeds directory {seeds_dir} does not exist"
    seed_files = list(seeds_dir.glob("*.json"))
    assert len(seed_files) == 1, f"expected exactly 1 seed file, found {seed_files}"

    seed_path = seeds_dir / f"{attempt_id}.json"
    assert seed_path.exists(), f"seed file {seed_path} was not created"
    assert seed_path.stat().st_mode & 0o777 == 0o600

    data = json.loads(seed_path.read_text(encoding="utf-8"))
    assert set(data.keys()) == {"attempt_id", "hash_seed"}
    assert data["attempt_id"] == attempt_id

    recorded_seed = data["hash_seed"]
    assert recorded_seed.isdecimal() and 0 <= int(recorded_seed) <= 4294967295

    assert prepared_seeds, "verifiers._prepare_run was never called"
    assert set(prepared_seeds) == {recorded_seed}
