"""Receiver side of #651: a v0.2 receipt verifies only when its bound verifiers ran each Python test
path as a whole file and Core required each bound verifier, in full and confidential mode."""

from __future__ import annotations

from pathlib import Path

import pytest

import tests.attest.binding_helpers as helpers
from cairnwork_attest.verify import verify_bundle
from cairnwork_attest.verify_coverage import targets
from tests.attest.binding_helpers import notarize_bound, policy, producer, receiver, sign_bound

PYTEST = ["python3", "-m", "pytest", "-q"]


def _verify(tmp_path: Path, verifiers: list, mode: str, **notarize) -> str:
    """Sign a receipt whose binding names ``verifiers`` and verify it with a pinning policy."""
    helpers.VERIFIERS[:] = verifiers
    p = producer(tmp_path)
    state = tmp_path / "state"
    bundle = sign_bound(state, notarize_bound(state, p, **notarize), tmp_path / "bundle.json")
    shas = (p["head"], p["tests"]) if mode == "full" else (p["head"],)
    repo = receiver(tmp_path, mode, p["work"], *shas)
    pol = policy(state, tmp_path, p["tests"])
    result = verify_bundle(
        bundle, public_key_pem=state / "public.pem", repo=repo, expected_candidate=p["head"],
        policy=pol,
    )  # fmt: skip
    assert result.code != "OK" or result.assurance is not None
    return result.code


@pytest.fixture(autouse=True)
def _restore_verifiers():
    saved = list(helpers.VERIFIERS)
    yield
    helpers.VERIFIERS[:] = saved


@pytest.mark.parametrize("mode", ["full", "confidential"])
def test_receipt_verifies_when_every_test_file_is_a_whole_target(tmp_path, mode):
    both = [["pytest-0", [*PYTEST, "tests/test_a.py", "tests/test_new.py"]]]
    assert _verify(tmp_path, both, mode) == "OK"


@pytest.mark.parametrize("mode", ["full", "confidential"])
@pytest.mark.parametrize(
    "args",
    [
        ["tests/test_new.py", "tests/test_a.py::test_a"],
        ["tests/test_new.py::test_new", "tests/test_a.py"],
    ],
)
def test_receipt_verifies_when_each_changed_test_is_a_node_target(tmp_path, mode, args):
    assert _verify(tmp_path, [["pytest-0", [*PYTEST, *args]]], mode) == "OK"


@pytest.mark.parametrize("mode", ["full", "confidential"])
def test_receipt_needs_a_target_for_each_bound_test_file(tmp_path, mode):
    one = [["pytest-0", [*PYTEST, "tests/test_new.py"]]]
    assert _verify(tmp_path, one, mode) == "TEST_PATH_NOT_TARGETED"


@pytest.mark.parametrize("mode,code", [("full", "TEST_PATH_NOT_TARGETED"), ("confidential", "OK")])
def test_receipt_reads_the_changed_tests_from_the_test_commit_in_full_mode(tmp_path, mode, code):
    """test_a.py's changed test is test_a; a target naming another test in that file covers it only
    on the signer's word, which confidential mode takes as it takes the test contents."""
    other = [["pytest-0", [*PYTEST, "tests/test_new.py", "tests/test_a.py::test_other"]]]
    assert _verify(tmp_path, other, mode) == code


def test_receipt_accepts_targets_split_across_required_verifiers(tmp_path):
    split = [
        ["pytest-0", [*PYTEST, "tests/test_a.py"]],
        ["pytest-1", [*PYTEST, "tests/test_new.py"]],
    ]
    assert _verify(tmp_path, split, "full", required=("pytest-0", "pytest-1")) == "OK"
    unrequired = tmp_path / "unrequired"
    unrequired.mkdir()
    assert _verify(unrequired, split, "full") == "VERIFIER_NOT_REQUIRED"


def test_targets_are_read_from_the_one_argv_shape_the_producer_accepts():
    assert targets([*PYTEST, "-p", "no:cacheprovider", "tests/test_a.py"]) == ("tests/test_a.py",)
    assert targets(["python3", "-m", "pytest", "tests/a.py::t"]) == ("tests/a.py::t",)
    assert targets([*PYTEST, "-k", "x", "tests/test_a.py"]) == ()
    assert targets([*PYTEST, "-p", "other", "tests/test_a.py"]) == ()
    assert targets(["pytest", "tests/test_a.py"]) == ()
