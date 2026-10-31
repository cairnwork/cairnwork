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
from cairnwork.reasons import why_not_passed
from cairnwork.verifiers import parse_verifier, run_verifiers
from cairnwork.verify_context import VerifyContext

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
    record = _record(collect_skipped=["tests/b.py"], files=["tests/a.py"], file_of=[0])
    verdict = report_guard.parse_verdict(record, "n")
    assert verdict.recorded and not verdict.ran
    assert verdict.collect_skipped == ("tests/b.py",)
    assert verdict.not_run(("tests/a.py",)) == ("tests/b.py",)


@pytest.mark.parametrize("bad", ["tests/b.py", [1], {"x": "y"}])
def test_a_malformed_skipped_collector_list_fails_closed(bad):
    verdict = report_guard.parse_verdict(_record(collect_skipped=bad), "n")
    assert not verdict.recorded and not verdict.ran


def test_a_test_owns_a_target_only_through_the_source_file_recorded_at_collection():
    node = "tests/a.py::test_op"
    for paths, owned in (({node: "tests/a.py"}, True), ({node: "tests/b.py"}, False), ({}, False)):
        verdict = report_guard.Verdict(True, {node: "passed"}, recorded=True, paths=paths)
        assert verdict.not_run(("tests/a.py",)) == (() if owned else ("tests/a.py",))


def test_the_record_names_each_collected_tests_file_by_index():
    record = _record(files=["tests/b.py", "tests/a.py"], file_of=[1])
    verdict = report_guard.parse_verdict(record, "n")
    assert verdict.ran and verdict.paths == {"tests/a.py::t": "tests/a.py"}
    assert verdict.not_run(("tests/a.py",)) == ()
    verdict = report_guard.parse_verdict(_record(files=["tests/a.py"], file_of=[-1]), "n")
    assert verdict.paths == {"tests/a.py::t": ""} and verdict.not_run(("tests/a.py",))
    verdict = report_guard.parse_verdict(_record(), "n")  # no file indices: owns nothing
    assert verdict.not_run(("tests/a.py",)) == ("tests/a.py",)


def test_a_record_naming_an_item_renamed_after_collection_is_not_a_pass():
    record = _record(files=["tests/a.py"], file_of=[0], renamed=["tests/a.py::t"])
    verdict = report_guard.parse_verdict(record, "n")
    assert verdict.recorded and not verdict.ran
    assert verdict.renamed == ("tests/a.py::t",)
    why = why_not_passed(
        ran_sandboxed=True, returncode=0, timed_out=False, mutated=(), survivors=0,
        log_too_big=False, tests_ran=False, renamed=verdict.renamed,
    )  # fmt: skip
    assert why == "items renamed after collection (1: tests/a.py??t)"  # _names masks colons


@pytest.mark.parametrize("bad", ["tests/a.py::t", [1], {"x": "y"}])
def test_a_malformed_renamed_list_fails_closed(bad):
    verdict = report_guard.parse_verdict(_record(renamed=bad), "n")
    assert not verdict.recorded and not verdict.ran


@pytest.mark.parametrize(
    ("files", "file_of"),
    [
        ("tests/a.py", [0]),
        ([1], [0]),
        (["tests/a.py"], "0"),
        (["tests/a.py"], [1]),
        (["tests/a.py"], [-2]),
        (["tests/a.py"], [True]),
        (["tests/a.py"], [0, 0]),
        (["tests/a.py"], []),
    ],
)
def test_a_malformed_source_file_index_fails_closed(files, file_of):
    verdict = report_guard.parse_verdict(_record(files=files, file_of=file_of), "n")
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
    paths = dict.fromkeys(outcomes, "tests/a.py")
    verdict = report_guard.Verdict(True, outcomes, recorded=True, paths=paths)
    assert verdict.not_run((target,)) == (() if owned else (target,))


# ---- a conftest that rewrites the collected items (run_verifiers, real sandbox) ----------

CORE_TWO = CORE_TEST + "def test_add_twice():\n    assert add(add(1, 1), 1) > 0\n"
# drops the target's own items and gives a passing item from another file a node id under it
RENAME = CONFTEST + (
    "def pytest_collection_modifyitems(items):\n"
    "    keep = [i for i in items if not i.nodeid.startswith('tests/test_ext.py')]\n"
    "    for item in keep:\n"
    "        if item.name == 'test_add_twice':\n"
    "            item._nodeid = 'tests/test_ext.py::test_mul'\n"
    "    items[:] = keep\n"
)
REORDER = CONFTEST + "def pytest_collection_modifyitems(items):\n    items.reverse()\n"
# an honest deselection: removes an item and reports it, renames nothing
DESELECT = CONFTEST + (
    "def pytest_collection_modifyitems(config, items):\n"
    "    dropped = [i for i in items if i.name == 'test_add_twice']\n"
    "    items[:] = [i for i in items if i not in dropped]\n"
    "    config.hook.pytest_deselected(items=dropped)\n"
)


@pytest.mark.sandbox
@pytest.mark.parametrize(
    ("conftest", "ext", "passes"),
    [
        (RENAME, EXT_WRONG, False),
        (REORDER, EXT, True),
        (REORDER, EXT_WRONG, False),
        (DESELECT, EXT, True),
        (DESELECT, EXT_WRONG, False),
    ],
    ids=[
        "renamed-into-target",
        "reordered-passing",
        "reordered-failing",
        "deselected-passing",
        "deselected-failing",
    ],
)
def test_a_target_is_owned_by_the_items_collected_from_its_file_not_by_their_node_id(
    repo, tmp_path, conftest, ext, passes
):
    commit_change(repo, {
        "src/calc.py": CALC, "src/calc_ext.py": EXT, "tests/conftest.py": CONFTEST,
        "tests/test_ext.py": IMPORTORSKIP, "tests/test_core.py": CORE_TWO,
    })  # fmt: skip
    candidate = commit_change(
        repo, {"src/calc.py": FIXED_ADD, "src/calc_ext.py": ext, "tests/conftest.py": conftest}
    )
    argv = ["python3", "-m", "pytest", "-q", "tests/test_core.py", "tests/test_ext.py"]
    spec = parse_verifier("pytest", argv)
    (run,) = run_verifiers(
        VerifyContext(repo, tmp_path / "runs", python=sys.executable), candidate, (spec,), "r1"
    )
    assert run.observation.passed is passes
    assert run.ran is passes
    if conftest is RENAME:
        assert run.returncode == 0  # pytest itself sees nothing wrong
        assert run.outcomes == {  # keyed by the node ids pytest collected
            "tests/test_core.py::test_add_positive": "passed",
            "tests/test_core.py::test_add_twice": "passed",
        }
        assert "items renamed after collection (1: tests/test_ext.py??test_mul)" in run.why
    if conftest is DESELECT and passes:
        assert "tests/test_core.py::test_add_twice" not in run.outcomes


# one file: its named test is dropped and a passing sibling takes over that test's node id
EXT_TWO = (
    "import pytest\nfrom calc_ext import mul\n"
    "def test_mul():\n    assert mul(2, 3) == 6\n"
    "@pytest.mark.parametrize('a', [0, 1])\n"
    "def test_mul_zero(a):\n    assert mul(0, 0) == 0\n"
)
SAME_FILE = CONFTEST + (
    "def pytest_collection_modifyitems(items):\n"
    "    keep = [i for i in items if i.name != 'test_mul']\n"
    "    for item in keep:\n"
    "        if item.name == 'test_mul_zero[1]':\n"
    "            item._nodeid = 'tests/test_ext.py::test_mul'\n"
    "    items[:] = keep\n"
)


@pytest.mark.sandbox
@pytest.mark.parametrize(
    ("conftest", "ext", "passes"),
    [(SAME_FILE, EXT_WRONG, False), (CONFTEST, EXT, True), (CONFTEST, EXT_WRONG, False)],
    ids=["renamed-within-the-file", "plain-passing", "plain-failing"],
)
def test_a_test_renamed_after_collection_fails_the_run_even_within_its_own_file(
    repo, tmp_path, conftest, ext, passes
):
    commit_change(repo, {
        "src/calc.py": CALC, "src/calc_ext.py": EXT, "tests/conftest.py": CONFTEST,
        "tests/test_ext.py": EXT_TWO, "tests/test_core.py": CORE_TEST,
    })  # fmt: skip
    candidate = commit_change(
        repo, {"src/calc.py": FIXED_ADD, "src/calc_ext.py": ext, "tests/conftest.py": conftest}
    )
    targets = [
        "tests/test_core.py",
        "tests/test_ext.py::test_mul",
        "tests/test_ext.py::test_mul_zero",
    ]
    spec = parse_verifier("pytest", ["python3", "-m", "pytest", "-q", *targets])
    (run,) = run_verifiers(
        VerifyContext(repo, tmp_path / "runs", python=sys.executable), candidate, (spec,), "r1"
    )
    assert run.observation.passed is passes
    assert run.ran is passes
    if conftest is SAME_FILE:
        assert run.returncode == 0  # pytest itself sees nothing wrong
        assert "tests/test_ext.py::test_mul_zero[1]" in run.outcomes
        assert "items renamed after collection (1: tests/test_ext.py??test_mul)" in run.why


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
    (run,) = run_verifiers(
        VerifyContext(repo, tmp_path / "runs", python=sys.executable), candidate, (spec,), "r1"
    )
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
