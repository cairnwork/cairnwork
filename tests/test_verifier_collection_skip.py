"""A verifier target must own a test that ran: a module skipped while collecting is not a pass.

pytest exits 0 when a module skips itself at collection (``pytest.importorskip`` or
``pytest.skip(..., allow_module_level=True)``), and that module's tests never reach the collected
list. Whether through an author verifier naming several files or the pass-to-pass verifier, the
run must not pass while one named file ran nothing.
"""

import json
import sys

import pytest
from conftest import commit_change
from test_deferred_tests import CHECK, REGRESSION, make_test_commit
from test_pipeline import EditWorker

from cairnwork import report_guard
from cairnwork.journal import Journal
from cairnwork.notary import Notary
from cairnwork.ownership import OwnershipLedger
from cairnwork.pipeline import TaskSpec, run_task
from cairnwork.verifiers import parse_verifier, run_verifiers

CONFTEST = (
    "import sys, pathlib\nsys.path.insert(0, str(pathlib.Path(__file__).parent.parent / 'src'))\n"
)
CALC = "def add(a, b):\n    return a + b + 1\n"
FIXED_ADD = "def add(a, b):\n    return a + b\n"
EXT = "def mul(a, b):\n    return a * b\n"
EXT_WRONG = "def mul(a, b):\n    return a + b\n"
EXT_WRONG_UNIMPORTABLE = "import _cw_missing_dependency\n" + EXT_WRONG
IMPORTORSKIP = (
    "import pytest\next = pytest.importorskip('calc_ext')\n"
    "def test_mul():\n    assert ext.mul(2, 3) == 6\n"
)
MODULE_SKIP = (
    "import pytest\ntry:\n    import calc_ext as ext\nexcept ImportError:\n"
    "    pytest.skip('no ext', allow_module_level=True)\n"
    "def test_mul():\n    assert ext.mul(2, 3) == 6\n"
)
CORE_TEST = "from calc import add\ndef test_add_positive():\n    assert add(2, 3) > 0\n"
SKIPPERS = pytest.mark.parametrize(
    "skipper", [IMPORTORSKIP, MODULE_SKIP], ids=["importorskip", "module-level-skip"]
)
# what the candidate leaves in src/calc_ext.py, and whether the run may pass
CASES = pytest.mark.parametrize(
    ("ext", "passes"),
    [(EXT_WRONG_UNIMPORTABLE, False), (EXT_WRONG, False), (EXT, True)],
    ids=["skipped-at-collection", "collected-and-failing", "collected-and-passing"],
)


def _base(repo, skipper):
    commit_change(repo, {
        "src/calc.py": CALC, "src/calc_ext.py": EXT, "tests/conftest.py": CONFTEST,
        "tests/test_ext.py": skipper, "tests/test_core.py": CORE_TEST,
    })  # fmt: skip


# ---- the record --------------------------------------------------------------------------


def _record(**extra):
    payload = {
        "nonce": "n",
        "collected": ["tests/a.py::t"],
        "outcomes": {"tests/a.py::t": "passed"},
    }
    return json.dumps(payload | extra).encode()


def test_a_record_naming_a_collector_skipped_at_collection_is_not_a_pass():
    verdict = report_guard.parse_verdict(_record(collect_skipped=["tests/b.py"]), "n")
    assert verdict.recorded and not verdict.ran
    assert verdict.collect_skipped == ("tests/b.py",)
    assert verdict.not_run(("tests/a.py",)) == ("tests/b.py",)


@pytest.mark.parametrize("bad", ["tests/b.py", [1], {"x": "y"}])
def test_a_malformed_skipped_collector_list_fails_closed(bad):
    verdict = report_guard.parse_verdict(_record(collect_skipped=bad), "n")
    assert not verdict.recorded and not verdict.ran


@pytest.mark.parametrize(
    ("target", "owned"),
    [
        ("tests/a.py", True),
        ("tests/a.py::test_op", True),
        ("tests/a.py::test_op[dbl]", True),
        ("tests/a.py::TestK", True),
        ("tests/a.py::TestK::test_m", True),
        ("tests/a.py::test_o", False),  # a prefix of a name is another test
        ("tests/a.p", False),
        ("tests/b.py", False),
    ],
)
def test_a_target_owns_its_node_its_children_and_its_parametrisations(target, owned):
    outcomes = {"tests/a.py::test_op[dbl]": "passed", "tests/a.py::TestK::test_m": "passed"}
    verdict = report_guard.Verdict(True, outcomes, recorded=True)
    assert verdict.not_run((target,)) == (() if owned else (target,))


# ---- the author's verifier (run_verifiers, real sandbox) ---------------------------------


@pytest.mark.sandbox
@SKIPPERS
@CASES
def test_a_verifier_target_whose_module_is_skipped_at_collection_does_not_count_as_passed(
    repo, tmp_path, skipper, ext, passes
):
    _base(repo, skipper)
    candidate = commit_change(repo, {"src/calc.py": FIXED_ADD, "src/calc_ext.py": ext})
    argv = ["python3", "-m", "pytest", "-q", "tests/test_core.py", "tests/test_ext.py"]
    spec = parse_verifier("pytest", argv)
    (run,) = run_verifiers(repo, candidate, (spec,), tmp_path / "runs", "r1", python=sys.executable)
    assert run.observation.passed is passes
    assert run.ran is passes
    if ext is EXT_WRONG_UNIMPORTABLE:
        assert run.returncode == 0  # pytest itself sees nothing wrong
        assert "no test ran for a target or skipped module" in run.why
        assert "tests/test_ext.py" in run.why


# ---- the pass-to-pass verifier (run_task, real sandbox) -----------------------------------


@pytest.mark.sandbox
@SKIPPERS
@CASES
def test_a_pass_to_pass_file_skipped_at_collection_does_not_count_as_passed(
    repo, tmp_path, skipper, ext, passes
):
    _base(repo, skipper)
    base, test_commit = make_test_commit(repo, {"tests/test_regression.py": REGRESSION})
    state = tmp_path / "state"
    state.mkdir()
    task = TaskSpec("k1", "card", "fix", "fix it", repo, base, ("src/calc.py", "src/calc_ext.py"),
                    CHECK, test_commit=test_commit, p2p_mode="whole-suite")  # fmt: skip
    result = run_task(
        task,
        ledger=OwnershipLedger(state / "own.sqlite"),
        notary=Notary(state / "notary.sqlite"),
        journal=Journal(state / "journal.sqlite"),
        worker=EditWorker({"src/calc.py": FIXED_ADD, "src/calc_ext.py": ext}),
        run_root=state / "runs",
    )
    assert result.p2p["targets"] == 2  # both base files pass on the base and are selected
    assert result.verified is passes
    assert result.status == ("COMPLETED" if passes else "FAILED")
    if ext is EXT_WRONG_UNIMPORTABLE:
        assert "p2p-whole-suite" in result.reason
        assert "tests/test_ext.py" in result.reason
