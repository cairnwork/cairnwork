"""#493: usefixtures is copied from the base config, so its fixture names resolve in the candidate
checkout. A candidate that redefines such a fixture to skip every test is still never verified:
a skipped test is not a passed one (deferred tests and p2p targets alike)."""

import pytest
from conftest import commit_change
from test_p2p import CALC, CONFTEST, FIX_ADD, SIBLING, run

pytestmark = pytest.mark.sandbox

GUARD = CONFTEST + "import pytest\n@pytest.fixture\ndef guard():\n    yield\n"
SKIPPING_GUARD = CONFTEST + "import pytest\n@pytest.fixture\ndef guard():\n    pytest.skip('x')\n"


@pytest.fixture
def project(repo):
    commit_change(
        repo,
        {
            "src/calc.py": CALC,
            "tests/conftest.py": GUARD,
            "tests/test_sibling.py": SIBLING,
            "pytest.ini": "[pytest]\nusefixtures = guard\n",
        },
    )
    return repo


def test_a_right_fix_with_the_base_fixture_is_verified(project, tmp_path):
    result = run(project, tmp_path, {"src/calc.py": FIX_ADD}, mode="whole-suite")
    assert result.status == "COMPLETED", result.reason


def test_a_candidate_fixture_that_skips_everything_is_never_verified(project, tmp_path):
    files = {"src/calc.py": FIX_ADD, "tests/conftest.py": SKIPPING_GUARD}
    result = run(project, tmp_path, files, mode="whole-suite")
    assert result.status != "COMPLETED" and not result.verified, result.reason
