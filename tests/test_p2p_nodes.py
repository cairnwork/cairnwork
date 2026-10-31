"""P2P binds the base-passing node ids (p2p.py): the unchanged tests of a test file the test
commit changes stay P2P tests, and each bound node id must run and pass on the candidate (one
that did not run is not a pass). Run end to end (run_task, real sandbox)."""

import pytest
from conftest import commit_change
from test_deferred_tests import CHECK, REGRESSION, make_test_commit
from test_pipeline import EditWorker

from cairnwork.journal import Journal
from cairnwork.notary import Notary
from cairnwork.ownership import OwnershipLedger
from cairnwork.pipeline import TaskSpec, run_task

pytestmark = pytest.mark.sandbox

CONFTEST = (
    "import sys, pathlib\nsys.path.insert(0, str(pathlib.Path(__file__).parent.parent / 'src'))\n"
)
CALC = "def add(a, b):\n    return a + b\ndef sub(a, b):\n    return a - b\n"
CALC_TESTS = (
    "import calc\ndef test_add():\n    assert calc.add(2, 3) == 5\n"
    "def test_sub():\n    assert calc.sub(5, 3) == 2\n"
)
MUL_TEST = "def test_mul():\n    assert calc.mul(2, 3) == 6\n"
MUL = "def mul(a, b):\n    return a * b\n"
SUB_OFF = CALC.replace("a - b", "a + b")


def _run(project, tmp_path, allowed, worker_files, test_files, check, mode="whole-suite"):
    base, test_commit = make_test_commit(project, test_files)
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    task = TaskSpec("k1", "card", "fix", "fix it", project, base, allowed, check,
                    test_commit=test_commit, p2p_mode=mode)  # fmt: skip
    return run_task(
        task,
        ledger=OwnershipLedger(state / "own.sqlite"),
        notary=Notary(state / "notary.sqlite"),
        journal=Journal(state / "journal.sqlite"),
        worker=EditWorker(worker_files),
        run_root=state / "runs",
    )


def _calc(repo, tmp_path, candidate, *, sibling, mode="whole-suite"):
    files = {"src/calc.py": CALC, "tests/conftest.py": CONFTEST, "tests/test_calc.py": CALC_TESTS}
    if sibling:
        files["tests/test_other.py"] = "def test_other():\n    assert True\n"
    commit_change(repo, files)
    check = (("pytest", ("python3", "-m", "pytest", "-q", "tests/test_calc.py::test_mul")),)
    return _run(repo, tmp_path, ("src/calc.py",), {"src/calc.py": candidate},
                {"tests/test_calc.py": CALC_TESTS + MUL_TEST}, check, mode)  # fmt: skip


@pytest.mark.parametrize("mode", ["whole-suite", "module-dir"])
@pytest.mark.parametrize("sibling", [False, True])
def test_an_unchanged_test_in_a_changed_test_file_still_guards_the_candidate(
    repo, tmp_path, sibling, mode
):
    result = _calc(repo, tmp_path, SUB_OFF + MUL, sibling=sibling, mode=mode)
    assert result.status == "FAILED" and not result.verified
    assert "tests/test_calc.py::test_sub" in result.p2p["rerun"]["failed"]


@pytest.mark.parametrize("sibling", [False, True])
def test_a_fix_that_keeps_the_unchanged_tests_passing_is_verified(repo, tmp_path, sibling):
    result = _calc(repo, tmp_path, CALC + MUL, sibling=sibling)
    assert result.status == "COMPLETED" and result.verified, result.reason
    # test_mul is the test commit's: the author's verifier judges it, P2P binds the others
    assert result.p2p["nodes"] == 2 + sibling
    assert "rerun" not in result.p2p and "missing" not in result.p2p


def test_a_test_the_test_commit_changes_is_left_to_the_authors_verifiers(repo, tmp_path):
    # test_sub is rewritten for the new behaviour; its base version would fail on a good fix
    commit_change(repo, {"src/calc.py": CALC, "tests/conftest.py": CONFTEST,
                         "tests/test_calc.py": CALC_TESTS})  # fmt: skip
    new_tests = CALC_TESTS.replace("sub(5, 3) == 2", "sub(5, 3) == -2")
    check = (("pytest", ("python3", "-m", "pytest", "-q", "tests/test_calc.py::test_sub")),)
    result = _run(repo, tmp_path, ("src/calc.py",),
                  {"src/calc.py": CALC.replace("a - b", "b - a")},
                  {"tests/test_calc.py": new_tests}, check)  # fmt: skip
    assert result.status == "COMPLETED" and result.verified, result.reason
    assert result.p2p["nodes"] == 1 and result.p2p["changed_by_test_commit"] == 1


def test_a_test_the_test_commit_removes_is_not_required(repo, tmp_path):
    commit_change(repo, {"src/calc.py": CALC, "tests/conftest.py": CONFTEST,
                         "tests/test_calc.py": CALC_TESTS})  # fmt: skip
    new_tests = CALC_TESTS.split("def test_sub")[0] + MUL_TEST
    check = (("pytest", ("python3", "-m", "pytest", "-q", "tests/test_calc.py::test_mul")),)
    result = _run(repo, tmp_path, ("src/calc.py",), {"src/calc.py": SUB_OFF + MUL},
                  {"tests/test_calc.py": new_tests}, check)  # fmt: skip
    assert result.status == "COMPLETED" and result.verified, result.reason
    assert result.p2p["nodes"] == 1 and result.p2p["changed_by_test_commit"] == 1


OPS = "def add(a, b):\n    return a + b + 1\nOPS = {'neg': lambda a: -a, 'dbl': lambda a: 2 * a}\n"
OPS_TEST = (
    "import pytest, calc\n"
    "@pytest.mark.parametrize('name', sorted(calc.OPS))\n"
    "def test_op(name):\n    assert calc.OPS[name](0) == 0\n"
)
FIXED = "def add(a, b):\n    return a + b\n"


@pytest.mark.parametrize(
    "ops, verified",
    [
        ("OPS = {'neg': lambda a: -a, 'dbl': lambda a: 2 * a}\n", True),
        ("OPS = {'neg': lambda a: -a, 'dbl': lambda a: 2 * a, 'sq': lambda a: a * a}\n", True),
        ("OPS = {'neg': lambda a: -a}\n", False),
    ],
    ids=["same", "one-more", "one-fewer"],
)
def test_every_base_passing_parametrisation_must_run_on_the_candidate(
    repo, tmp_path, ops, verified
):
    commit_change(repo, {"src/calc.py": OPS, "tests/conftest.py": CONFTEST,
                         "tests/test_ops.py": OPS_TEST})  # fmt: skip
    result = _run(repo, tmp_path, ("src/calc.py",), {"src/calc.py": FIXED + ops},
                  {"tests/test_regression.py": REGRESSION}, CHECK)  # fmt: skip
    assert result.verified is verified, result.reason
    assert result.p2p["nodes"] == 2
    if not verified:
        assert result.status == "FAILED"
        assert result.p2p["missing"] == ["tests/test_ops.py::test_op[dbl]"]
        assert "did not run on the candidate" in result.reason
        assert "rerun" not in result.p2p  # not a flaky test failure: no re-run
