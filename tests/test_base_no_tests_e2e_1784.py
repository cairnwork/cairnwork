"""#1784: a verifier target that holds no test (a conftest passed to --verifier) ends the base run
with pytest exit 5. The run says so as UNCOMPARABLE BASE_NO_TESTS and names the target; the same
bundle with only the test file as the target is judged as before. Real Seatbelt, bundle source."""

import json

import pytest
from test_deferred_store import make_bundle
from verify_helpers import base_of, candidate, cli, verify

pytestmark = pytest.mark.sandbox
SETUP = (
    "import sys, pathlib\nsys.path.insert(0, str(pathlib.Path(__file__).parent.parent / 'src'))\n"
)
TEST = "from calc import add\ndef test_regression():\n    assert add(1, 1) == 2\n"
CONFTEST_V = "python3 -m pytest -q tests/conftest.py"
TEST_V = "python3 -m pytest -q tests/test_regression.py"
GOOD = "def add(a, b):\n    return a + b\n"
WRONG = "def add(a, b):\n    return 0\n"


def imported(project, state, tmp_path, extra=False):
    """The base (``project``) already holds tests/conftest.py; the test commit carries the same
    bytes, so it is not in the diff, and a new test."""
    files = {"tests/conftest.py": SETUP, "tests/test_regression.py": TEST}
    if extra:  # a file only the test commit has, that holds no test
        files["tests/test_only_in_commit.py"] = "X = 1\n"
    base, sha, bundle = make_bundle(project, files, tmp_path)
    (state / "config.json").write_text(
        json.dumps({"verifier_account": False, "test_source": "bundle"})
    )
    code, out = cli(state, "import-tests", "--repo-path", str(project), "--bundle", str(bundle))
    assert code == 0 and out == {"imported": sha}
    return base_of(project), sha


def text(out):
    return json.dumps(out)


def test_a_conftest_target_is_named_in_the_base_no_tests_reason(project, state, tmp_path):
    base, sha = imported(project, state, tmp_path)
    cand = candidate(project, base, {"src/calc.py": GOOD})
    code, out = verify(state, project, base, cand, "--test-commit", sha,
                       tests=(CONFTEST_V, TEST_V))  # fmt: skip
    assert code != 0 and out["status"] == "UNCOMPARABLE"
    assert out["uncomparable"]["code"] == "BASE_NO_TESTS"
    assert out["uncomparable"]["detail"] == (
        "pytest-0: BASE_NO_TESTS (exit 5); pytest collected no test in 1 target(s)"
    )  # a --test-commit run is hidden: the count, no file name, no pytest output


def test_a_file_only_the_test_commit_has_is_not_named(project, state, tmp_path):
    base, sha = imported(project, state, tmp_path, extra=True)
    cand = candidate(project, base, {"src/calc.py": GOOD})
    only = "python3 -m pytest -q tests/test_only_in_commit.py"
    code, out = verify(state, project, base, cand, "--test-commit", sha, tests=(only, TEST_V))
    assert out["uncomparable"]["code"] == "BASE_NO_TESTS"
    assert "test_only_in_commit" not in out["uncomparable"]["detail"]
    assert out["uncomparable"]["detail"].endswith("collected no test in 1 target(s)")


@pytest.mark.parametrize("good", [True, False], ids=["correct", "wrong"])
def test_the_test_file_alone_is_judged_as_before(project, state, tmp_path, good):
    base, sha = imported(project, state, tmp_path)
    cand = candidate(project, base, {"src/calc.py": GOOD if good else WRONG})
    code, out = verify(state, project, base, cand, "--test-commit", sha, tests=(TEST_V,))
    assert out["status"] == ("VERIFIED" if good else "FAILED"), out
