"""#1065 evidence: candidate-owned sys.path edits can expose a shadow before deferred tests import.

Every case runs through both real sandboxed paths: ``run_task`` (worker) and the ``verify`` helper
which calls ``verify_candidate`` through its CLI entry. E1 documents today's false green: the
candidate is wrong but the test imports its fake ``fractions`` module and passes; the assertion is
the required fix outcome (FAILED / not VERIFIED), so strict xfail becomes an XPASS failure after a
fix. E2 is the same wrong fix without a shadow. E3 and E4 are honest controls for a sys.path edit
alone and a harmless module alongside a correct fix.
"""

import json

import pytest
from conftest import commit_change, git
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
}
DEFERRED = (
    "from pkg.calc import half\n"
    "from fractions import Fraction\n\n\n"
    "def test_half():\n    assert Fraction(*half()) == Fraction(1, 2)\n"
)
SYSPATH = "import os\nimport sys\nsys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n"
WRONG = {"pkg/calc.py": SYSPATH + "def half():\n    return (1, 3)\n"}
CORRECT = {"pkg/calc.py": SYSPATH + "def half():\n    return (1, 2)\n"}
SHADOW = {
    "pkg/fractions.py": (
        "class Fraction:\n"
        "    def __init__(self, *args):\n        pass\n\n"
        "    def __eq__(self, other):\n        return True\n"
    )
}
HARMLESS = {"pkg/harmless.py": "VALUE = 'unused'\n"}
ALLOWED = ("pkg/calc.py", "pkg/fractions.py", "pkg/harmless.py")
VERIFY = (("pytest", ("python3", "-m", "pytest", "-q", "pkg/tests/test_half.py")),)


def _worker(repo, tmp_path, edit, key):
    commit_change(repo, BASE)
    base, tests = make_test_commit(repo, {"pkg/tests/test_half.py": DEFERRED})
    state = tmp_path / key
    state.mkdir()
    task = TaskSpec(
        key,
        "card-" + key,
        "fix half",
        "half() is wrong",
        repo,
        base,
        ALLOWED,
        VERIFY,
        test_commit=tests,
        test_roots=("pkg/tests",),
    )
    return run_task(
        task,
        ledger=OwnershipLedger(state / "own.sqlite"),
        notary=Notary(state / "notary.sqlite"),
        journal=Journal(state / "journal.sqlite"),
        worker=EditWorker(edit),
        run_root=state / "runs",
    )


def _verify(project, state, edit):
    commit_change(project, BASE)
    base = git(project, "rev-parse", "main")
    _base, tests = make_test_commit(project, {"pkg/tests/test_half.py": DEFERRED})
    cand = candidate(project, base, edit)
    command = "python3 -m pytest -q pkg/tests/test_half.py"
    return verify(
        state,
        project,
        base,
        cand,
        "--test-commit",
        tests,
        allowed=ALLOWED,
        tests=(command,),
    )


@pytest.mark.parametrize("path", ["worker run_task", "verify_candidate via verify helper"])
@pytest.mark.xfail(strict=True, reason="#1065 false green on main")
def test_e1_wrong_fix_cannot_pass_by_shadowing_fractions(repo, tmp_path, path):
    """E1 covers worker ``run_task`` and verify ``verify_candidate``.

    Today worker is COMPLETED and verify is VERIFIED; a fix must give worker FAILED and verify
    not VERIFIED.
    """
    edit = WRONG | SHADOW
    if path == "worker run_task":
        result = _worker(repo, tmp_path, edit, "e1-worker")
        assert result.status == "FAILED", result.reason
    else:
        state = tmp_path / "state"
        state.mkdir(mode=0o700)
        (state / "config.json").write_text(
            json.dumps({"verifier_account": False, "test_roots": ["pkg/tests"]})
        )
        (state / "ACCEPTING").touch()
        _code, out = _verify(repo, state, edit)
        assert out["status"] == "FAILED" and out["verified"] is False, out


@pytest.mark.parametrize("path", ["worker run_task", "verify_candidate via verify helper"])
def test_e2_wrong_fix_without_shadow_is_rejected(repo, tmp_path, path):
    """E2 covers worker ``run_task`` and verify ``verify_candidate``.

    Today both paths are FAILED; a fix must continue to give FAILED on both.
    """
    if path == "worker run_task":
        result = _worker(repo, tmp_path, WRONG, "e2-worker")
        assert result.status == "FAILED", result.reason
    else:
        state = tmp_path / "state"
        state.mkdir(mode=0o700)
        (state / "config.json").write_text(
            json.dumps({"verifier_account": False, "test_roots": ["pkg/tests"]})
        )
        (state / "ACCEPTING").touch()
        _code, out = _verify(repo, state, WRONG)
        assert out["status"] == "FAILED" and out["verified"] is False, out


@pytest.mark.parametrize("path", ["worker run_task", "verify_candidate via verify helper"])
def test_e3_honest_fix_with_own_directory_sys_path_edit_verifies(repo, tmp_path, path):
    """E3 covers worker ``run_task`` and verify ``verify_candidate``.

    Today worker is COMPLETED and verify is VERIFIED; a fix must preserve both verdicts.
    """
    if path == "worker run_task":
        result = _worker(repo, tmp_path, CORRECT, "e3-worker")
        assert result.status == "COMPLETED", result.reason
    else:
        state = tmp_path / "state"
        state.mkdir(mode=0o700)
        (state / "config.json").write_text(
            json.dumps({"verifier_account": False, "test_roots": ["pkg/tests"]})
        )
        (state / "ACCEPTING").touch()
        _code, out = _verify(repo, state, CORRECT)
        assert out["status"] == "VERIFIED" and out["verified"] is True, out


@pytest.mark.parametrize("path", ["worker run_task", "verify_candidate via verify helper"])
def test_e4_honest_fix_with_sys_path_and_harmless_module_verifies(repo, tmp_path, path):
    """E4 covers worker ``run_task`` and verify ``verify_candidate``.

    Today worker is COMPLETED and verify is VERIFIED; a fix must preserve both verdicts.
    """
    edit = CORRECT | HARMLESS
    if path == "worker run_task":
        result = _worker(repo, tmp_path, edit, "e4-worker")
        assert result.status == "COMPLETED", result.reason
    else:
        state = tmp_path / "state"
        state.mkdir(mode=0o700)
        (state / "config.json").write_text(
            json.dumps({"verifier_account": False, "test_roots": ["pkg/tests"]})
        )
        (state / "ACCEPTING").touch()
        _code, out = _verify(repo, state, edit)
        assert out["status"] == "VERIFIED" and out["verified"] is True, out
