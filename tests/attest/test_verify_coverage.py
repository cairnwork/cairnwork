"""Receiver side of #651: a v0.2 receipt verifies only when its bound verifiers ran each Python test
path as a whole file and Core required each bound verifier, in full and confidential mode."""

from __future__ import annotations

from pathlib import Path

import pytest

import tests.attest.binding_helpers as helpers
from cairnwork_attest.verify import verify_bundle
from cairnwork_attest.verify_coverage import targets
from cairnwork_attest.verify_response import VerifyResult
from tests.attest.binding_helpers import notarize_bound, policy, producer, receiver, sign_bound

PYTEST = ["python3", "-m", "pytest", "-q"]


def _verify(tmp_path: Path, verifiers: list, mode: str, **notarize) -> str:
    return _result(tmp_path, verifiers, mode, **notarize).code


def _result(tmp_path: Path, verifiers: list, mode: str, **notarize) -> VerifyResult:
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
    return result


@pytest.fixture(autouse=True)
def _restore_verifiers():
    saved, tests = list(helpers.VERIFIERS), dict(helpers.TESTS)
    yield
    helpers.VERIFIERS[:] = saved
    helpers.TESTS.clear()
    helpers.TESTS.update(tests)


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
        ["tests/test_new.py", "tests/test_a.py::test_a[1]"],  # one case names its test (#714)
    ],
)
def test_receipt_verifies_when_each_changed_test_is_a_node_target(tmp_path, mode, args):
    assert _verify(tmp_path, [["pytest-0", [*PYTEST, *args]]], mode) == "OK"


@pytest.mark.parametrize("mode", ["full", "confidential"])
def test_receipt_needs_a_target_for_each_bound_test_file(tmp_path, mode):
    one = [["pytest-0", [*PYTEST, "tests/test_new.py"]]]
    assert _verify(tmp_path, one, mode) == "TEST_PATH_NOT_TARGETED"


@pytest.mark.parametrize("mode", ["full", "confidential"])
@pytest.mark.parametrize(
    "odd", ["tests/test_a.py[x]", "tests/test_a.py::test_a[x", "tests/test_a.py::test_a[x]::y"]
)
def test_receipt_refuses_a_target_outside_the_producer_grammar(tmp_path, mode, odd):
    """The producer refuses these targets; the receiver must not read them as naming a file or a
    test (#714)."""
    one = [["pytest-0", [*PYTEST, "tests/test_new.py", odd]]]
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
    # the producer grammar applies here too (#714): one bad target covers nothing
    assert targets([*PYTEST, "tests/test_a.py::test_a[x]"]) == ("tests/test_a.py::test_a[x]",)
    for bad in ("tests/test_a.py::test_a[x", "tests/test_a.py::test_a[x]::y", "tests/test_a.py[x]"):
        assert targets([*PYTEST, "tests/test_new.py", bad]) == ()
    # the grammar is not tied to a `tests` root (#722)
    assert targets([*PYTEST, "pkg/test/test_a.py::test_a[1]"]) == ("pkg/test/test_a.py::test_a[1]",)
    assert targets([*PYTEST, "pkg/test/test_a.py[x]"]) == ()


# #660: what the target names alone do not show, read from the test commit (full mode)
INHERITED = (
    "def test_a():\n    assert 1\nclass TestK:\n    def test_m(self):\n        pass\n"
    "class TestK2(TestK):\n    pass\n"
)
NOT_COLLECTED = (
    "def test_a():\n    assert 1\nclass TestK:\n    def __init__(self):\n        pass\n"
    "    def test_m(self):\n        pass\n"
)
PARENT_ONLY = ["tests/test_new.py", "tests/test_a.py::test_a", "tests/test_a.py::TestK::test_m"]


@pytest.mark.parametrize("mode,code", [("full", "TEST_PATH_NOT_TARGETED"), ("confidential", "OK")])
def test_receipt_needs_a_target_for_each_node_id_a_new_test_is_collected_under(
    tmp_path, mode, code
):
    """Full mode reads every node id pytest collects the new test under; confidential mode cannot
    read the file and takes the signer's word."""
    helpers.TESTS["tests/test_a.py"] = INHERITED
    got = _result(tmp_path, [["pytest-0", [*PYTEST, *PARENT_ONLY]]], mode)
    assert got.code == code
    if mode == "full":
        assert got.detail == "no bound verifier runs these: ['tests/test_a.py']"
    each = tmp_path / "each"
    each.mkdir()
    both = [["pytest-0", [*PYTEST, *PARENT_ONLY, "tests/test_a.py::TestK2"]]]
    assert _verify(each, both, mode) == "OK"


@pytest.mark.parametrize("mode,code", [("full", "TEST_PATH_NOT_TARGETED"), ("confidential", "OK")])
def test_receipt_refuses_a_new_test_pytest_does_not_collect(tmp_path, mode, code):
    """Naming the whole file does not run a test pytest does not collect. The receiver reads that
    from the test commit in full mode only."""
    helpers.TESTS["tests/test_a.py"] = NOT_COLLECTED
    whole = [["pytest-0", [*PYTEST, "tests/test_a.py", "tests/test_new.py"]]]
    got = _result(tmp_path, whole, mode)
    assert got.code == code
    if mode == "full":
        assert got.detail.startswith("cannot read the bound tests: DEFERRED_TESTS_NOT_COLLECTED: ")
        assert "tests/test_a.py::TestK::test_m" in got.detail
