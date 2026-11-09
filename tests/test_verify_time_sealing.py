"""#386: a sealed package (#382) is judged again once the hidden test commit is fetched.

Intake never fetches the deferred test commit, so it cannot see a test that puts a sealed
package on sys.path. A candidate that adds a stdlib-named module in that package would then take
over the module for those tests. The run now refuses such a candidate after the fetch
(TEST_COMMIT_UNSEALS_CANDIDATE); an honest fix in the same package, or a shadow-named file
the tests never reach, still verifies. Real Seatbelt end to end, on the worker path (run_task)
and the verify path (verify_cli run --test-commit), which share the check in tests_with_teeth."""

import json

import pytest
from conftest import commit_change
from test_deferred_tests import make_test_commit
from test_pipeline import EditWorker
from verify_helpers import candidate, verify

from cairnwork.journal import Journal
from cairnwork.notary import Notary
from cairnwork.ownership import OwnershipLedger
from cairnwork.pipeline import TaskSpec, run_task

pytestmark = pytest.mark.sandbox

BASE = {
    "pkg/__init__.py": "",
    "pkg/calc.py": "def half():\n    return (1, 3)\n",
    "pkg/tests/__init__.py": "",
    "pkg/tests/test_base.py": "def test_ok():\n    assert True\n",
}
UNSEALING = (  # the hidden test puts pkg itself in front of the standard library
    "import os\nimport sys\n\n"
    "sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))\n"
    "import fractions  # noqa: E402\n\nfrom pkg.calc import half  # noqa: E402\n\n\n"
    "def test_half():\n    assert fractions.Fraction(*half()) == fractions.Fraction(1, 2)\n"
)
PLAIN = UNSEALING.replace(
    "sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))\n", ""
)
FAKE = "class Fraction:\n    def __init__(self, *args):\n        pass\n\n    def __eq__(self, other):\n        return True\n"  # noqa: E501
FIX = {"pkg/calc.py": "def half():\n    return (1, 2)\n"}
SHADOW = {"pkg/fractions.py": FAKE}
VERIFY = (("pytest", ("python3", "-m", "pytest", "-q", "pkg/tests/test_half.py")),)
ALLOWED = ("pkg/calc.py", "pkg/fractions.py")


def _run(repo, tmp_path, test, edit, key):
    commit_change(repo, BASE)
    base, tests = make_test_commit(repo, {"pkg/tests/test_half.py": test})
    state = tmp_path / key
    state.mkdir()
    task = TaskSpec(key, "card-" + key, "fix half", "half() is wrong", repo, base, ALLOWED, VERIFY,
                    test_commit=tests, test_roots=("pkg/tests",))  # fmt: skip
    result = run_task(
        task,
        ledger=OwnershipLedger(state / "own.sqlite"),
        notary=Notary(state / "notary.sqlite"),
        journal=Journal(state / "journal.sqlite"),
        worker=EditWorker(edit),
        run_root=state / "runs",
    )
    return result


def test_a_shadowing_candidate_is_refused_once_the_tests_unseal_its_package(repo, tmp_path):
    result = _run(repo, tmp_path, UNSEALING, SHADOW, "shadow-unsealed")
    assert result.status == "FAILED", result.reason
    assert "TEST_COMMIT_UNSEALS_CANDIDATE: pkg/fractions.py" in result.reason


def test_an_honest_fix_in_the_package_still_verifies_with_the_unsealing_tests(repo, tmp_path):
    result = _run(repo, tmp_path, UNSEALING, FIX, "fix-unsealed")
    assert result.status == "COMPLETED", result.reason


@pytest.mark.parametrize(("edit", "key"), [(FIX, "fix-sealed"), (FIX | SHADOW, "both-sealed")])
def test_a_sealed_package_the_tests_leave_sealed_keeps_its_shadow_names_free(
    repo, tmp_path, edit, key
):
    result = _run(repo, tmp_path, PLAIN, edit, key)
    assert result.status == "COMPLETED", result.reason


@pytest.mark.parametrize(
    ("edit", "verdict"), [(SHADOW, "REFUSED"), (FIX, "VERIFIED"), (FIX | SHADOW, "REFUSED")]
)
def test_the_verify_path_judges_the_candidate_with_the_fetched_tests(repo, tmp_path, edit, verdict):
    commit_change(repo, BASE)
    base, tests = make_test_commit(repo, {"pkg/tests/test_half.py": UNSEALING})
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    config = {"verifier_account": False, "test_roots": ["pkg/tests"]}
    (state / "config.json").write_text(json.dumps(config))
    (state / "ACCEPTING").touch()
    cand = candidate(repo, base, edit)
    argv = "python3 -m pytest -q pkg/tests/test_half.py"
    _code, out = verify(state, repo, base, cand, "--test-commit", tests, allowed=ALLOWED,
                        tests=(argv,))  # fmt: skip
    assert out["status"] == verdict, out
    if verdict == "REFUSED":
        assert "TEST_COMMIT_UNSEALS_CANDIDATE" in json.dumps(out)
