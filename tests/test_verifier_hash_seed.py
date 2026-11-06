import json
import sys

import pytest
from conftest import commit_change, git

from cairnwork.blackbox_runner import HASH_SEED
from cairnwork.verifiers import parse_verifier, run_verifiers
from cairnwork.verify_context import VerifyContext, new_hash_seed, record_hash_seed

pytestmark = pytest.mark.sandbox


def test_one_attempt_uses_same_seed_for_base_and_head(repo, tmp_path):
    seed = new_hash_seed()
    commit_change(
        repo,
        {
            "tests/test_seed.py": (
                "import os\n"
                f"def test_attempt_seed(): assert os.environ['PYTHONHASHSEED'] == {seed!r}\n"
            )
        },
    )
    base = git(repo, "rev-parse", "HEAD")
    commit_change(repo, {"src/a.py": "x = 2\n"})
    head = git(repo, "rev-parse", "HEAD")
    ctx = VerifyContext(repo, tmp_path / "runs", python=sys.executable, hash_seed=seed)
    spec = parse_verifier("pytest", ["python3", "-m", "pytest", "tests/test_seed.py"])
    first = run_verifiers(ctx, base, (spec,), "attempt-b")[0]
    second = run_verifiers(ctx, head, (spec,), "attempt-v")[0]
    assert first.observation.passed and second.observation.passed


def test_attempt_seeds_are_distinct_random_uint32_values():
    first, second = new_hash_seed(), new_hash_seed()
    assert first.isdecimal() and second.isdecimal()
    assert 0 <= int(first) <= 4294967295
    assert 0 <= int(second) <= 4294967295
    assert first != second
    assert first != HASH_SEED and second != HASH_SEED


def test_seed_is_kept_in_private_attempt_record(repo, tmp_path):
    ctx = VerifyContext(repo, tmp_path / "state" / "runs", "a123", hash_seed="123456")
    record_hash_seed(ctx)
    path = tmp_path / "state" / "verifier-seeds" / "a123.json"
    assert json.loads(path.read_text()) == {"attempt_id": "a123", "hash_seed": "123456"}
    assert path.stat().st_mode & 0o777 == 0o600


def test_run_verifiers_refuses_missing_seed(repo, tmp_path):
    ctx = VerifyContext(repo, tmp_path / "runs", hash_seed="")
    spec = parse_verifier("pytest", ["python3", "-m", "pytest", "tests/test_a.py"])
    with pytest.raises(RuntimeError, match="hash seed"):
        run_verifiers(ctx, git(repo, "rev-parse", "HEAD"), (spec,), "missing")


def test_each_attempt_starts_with_a_fresh_recorded_seed(repo, tmp_path):
    ctx = VerifyContext(repo, tmp_path / "state" / "runs")
    first, second = ctx.started("a1"), ctx.started("a2")
    assert (first.attempt_id, second.attempt_id) == ("a1", "a2")
    assert first.hash_seed != second.hash_seed
    for started in (first, second):
        path = tmp_path / "state" / "verifier-seeds" / f"{started.attempt_id}.json"
        assert json.loads(path.read_text())["hash_seed"] == started.hash_seed
