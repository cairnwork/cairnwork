"""#870: the verifier's own pytest run fixes PYTHONHASHSEED (as the blackbox run does, #815), so a
test that depends on set or str hash order gives the same result on the base and the head run."""

import sys

import pytest
from conftest import commit_change

from cairnwork.blackbox_runner import HASH_SEED
from cairnwork.verifiers import _prepare_run, parse_verifier, run_verifiers
from cairnwork.verify_context import VerifyContext

PYTEST = ["python3", "-m", "pytest"]
ORDER = ["x_c", "x_b", "y_b", "y_a", "x_a"]  # list of this set under PYTHONHASHSEED=0
TESTS = (
    "import os\n"
    f"def test_seed():\n    assert os.environ.get('PYTHONHASHSEED') == {HASH_SEED!r}\n"
    "def test_set_order():\n"
    f"    assert list({{'x_a', 'x_b', 'x_c', 'y_a', 'y_b'}}) == {ORDER!r}\n"
)


def _passed(repo, tmp_path, commit, number):
    spec = parse_verifier("pytest", PYTEST + ["tests/test_order.py"])
    ctx = VerifyContext(repo, tmp_path / f"runs{number}", python=sys.executable)
    return run_verifiers(ctx, commit, (spec,), f"r{number}")[0].observation.passed


@pytest.mark.sandbox
def test_hash_order_tests_agree_on_base_and_head(repo, tmp_path):
    base = commit_change(repo, {"tests/test_order.py": TESTS})
    head = commit_change(repo, {"src/unrelated.py": "X = 1\n"})
    results = [_passed(repo, tmp_path, c, n) for n, c in enumerate((base, head, base, head))]
    assert results == [True, True, True, True]  # unset seed: the order differs from run to run


def test_prepared_environment_has_the_fixed_seed(tmp_path):
    private = tmp_path / "private"
    private.mkdir()
    env = _prepare_run(private, private / "pytest.ini", b"[pytest]\n")[3]
    assert env["PYTHONHASHSEED"] == HASH_SEED
