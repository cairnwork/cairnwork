"""#798: with a conftest in the test commit, a base test file whose module-level code the test
commit edits is left out of the differential probe's comparison only. P2P still runs its base
tests on the candidate, so a candidate that breaks one fails, as it does without a conftest
(review of #895)."""

import pytest
from conftest import commit_change
from conftest_scenarios import BASE_CONFTEST, BASE_TEST, FIXTURE, PLAIN, USES_FIXTURE
from test_deferred_tests import SeeingWorker, make_test_commit, run

pytestmark = pytest.mark.sandbox

CALC2 = "def add(a, b):\n    return a + b + 1\n\n\ndef mul(a, b):\n    return a * b\n"
MUL = "from calc import mul\n\n\ndef test_mul():\n    assert mul(2, 3) == 6\n"
BREAKS_MUL = {"src/calc.py": "def add(a, b):\n    return a + b\n\n\ndef mul(a, b):\n    return 0\n"}
NEW_TEST = "\n\ndef test_new2():\n    assert True\n"
VERIFIERS = (
    (
        "pytest",
        ("python3", "-m", "pytest", "-q", "tests/test_regression.py",
         "tests/other/test_mul.py::test_new2"),
    ),
)  # fmt: skip
CASES = {
    "a conftest and a module-level edit of a base test file": (True, "import os\n" + MUL),
    "no conftest and a module-level edit": (False, "import os\n" + MUL),
    "a conftest and a test added to a base test file": (True, MUL),
}


@pytest.fixture
def project(repo):
    files = {"src/calc.py": CALC2, "tests/conftest.py": BASE_CONFTEST}
    commit_change(repo, files | {"tests/test_base.py": BASE_TEST, "tests/other/test_mul.py": MUL})
    return repo


@pytest.mark.parametrize("name", list(CASES))
def test_a_candidate_that_breaks_a_base_test_of_an_edited_file_fails(project, tmp_path, name):
    with_conftest, mul_text = CASES[name]
    files = {
        "tests/test_regression.py": USES_FIXTURE if with_conftest else PLAIN,
        "tests/other/test_mul.py": mul_text + NEW_TEST,
    }
    if with_conftest:
        files["tests/conftest.py"] = BASE_CONFTEST + "\n" + FIXTURE
    base, tests = make_test_commit(project, files)
    result = run(project, tmp_path, base, tests, SeeingWorker(BREAKS_MUL), verifiers=VERIFIERS,
                 p2p_mode="whole-suite", p2p_budget=300)  # fmt: skip
    assert result.status == "FAILED", result.reason
    assert "test_mul" in result.reason or "p2p" in result.reason, result.reason
