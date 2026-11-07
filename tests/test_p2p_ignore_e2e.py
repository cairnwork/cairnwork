"""#956 item 2, real pipeline runs (Seatbelt): a badly named base test directory that the base's
conftest leaves out with collect_ignore is no reason to refuse; one pytest still collects is.
On main every row ended UNCOMPARABLE P2P_BASE_UNTARGETABLE before the worker started."""

import pytest
from conftest import commit_change
from test_p2p import CALC, CONFTEST, FIX_ADD, FIX_BREAKS_NEG, SIBLING, run

pytestmark = pytest.mark.sandbox

ADD = "from calc import add\ndef test_add():\n    assert add(2, 3)\n"
FAR = "tests/v1.0/test_far.py"
IGNORES = CONFTEST + "collect_ignore = ['v1.0']\n"


@pytest.fixture
def project(repo):  # test_p2p's project; tests/test_sibling.py binds neg()
    commit_change(repo, {"src/calc.py": CALC, "tests/test_calc.py": ADD,
                         "tests/test_sibling.py": SIBLING})  # fmt: skip
    return repo


@pytest.mark.parametrize("fix", ["correct", "wrong"])
def test_a_directory_the_base_ignores_is_no_reason_to_refuse(project, tmp_path, fix):
    """Correct fix: COMPLETED. Wrong fix (breaks neg()): still caught by tests/test_sibling.py."""
    worker = {"src/calc.py": FIX_ADD if fix == "correct" else FIX_BREAKS_NEG}
    extra = {"tests/conftest.py": IGNORES, FAR: SIBLING}
    result = run(project, tmp_path, worker, mode="whole-suite", extra=extra)
    if fix == "correct":
        assert result.status == "COMPLETED" and result.verified, result.reason
    else:
        assert result.status == "FAILED" and not result.verified, result.reason
        assert "P2P_BASE_UNTARGETABLE" not in result.reason, result.reason


def test_a_directory_a_nearer_conftest_collects_again_is_still_refused(project, tmp_path):
    """The root conftest ignores tests/v1.0, but tests/conftest.py has its own (empty) list, which
    pytest uses: it collects tests/v1.0/test_far.py, so it is never dropped."""
    extra = {"conftest.py": "collect_ignore = ['tests/v1.0']\n",
             "tests/conftest.py": CONFTEST + "collect_ignore = []\n", FAR: SIBLING}  # fmt: skip
    result = run(project, tmp_path, {"src/calc.py": FIX_ADD}, mode="whole-suite", extra=extra)
    assert result.status == "FAILED" and not result.verified, result.reason
    assert result.reason.startswith("UNCOMPARABLE: P2P_BASE_UNTARGETABLE: "), result.reason
