"""Real Seatbelt coverage for a pre-existing environment-skip marker."""

import re

import pytest
from conftest import commit_change, git
from verify_helpers import FIX, candidate, verify

from cairnwork_attest.record import RecordNotFound, read_record

pytestmark = pytest.mark.sandbox
REASON = "needs macOS sandbox-exec"
TEST = (
    "import pytest\n"
    f"@pytest.mark.skipif(True, reason={REASON!r})\n"
    "def test_env():\n    assert False\n"
)
COMMAND = "python3 -m pytest -q tests/test_runner.py"


def _base(project):
    commit_change(
        project,
        {
            "tests/test_env.py": TEST.replace("test_env", "test_env_case"),
            "tests/test_runner.py": "from test_env import test_env_case\n",
        },
        delete=("tests/test_calc.py", "tests/test_skip.py"),
    )
    return git(project, "rev-parse", "HEAD").strip()


def _assert_no_receipt(state, out):
    with pytest.raises(RecordNotFound):
        read_record(state / "notary.sqlite", out["task_id"], out["attempt_id"])


def test_unchanged_test_with_environment_skip_is_uncomparable(project, state):
    base = _base(project)
    cand = candidate(project, base, {"src/calc.py": FIX})
    code, out = verify(state, project, base, cand, tests=(COMMAND,))
    assert code == 2 and out["status"] == "UNCOMPARABLE"
    assert out["uncomparable"]["code"] == "TESTS_SKIPPED_ENVIRONMENT"
    assert REASON in out["reason"] and not re.search(r"/(Users|private|var)/", out["reason"])
    _assert_no_receipt(state, out)


def test_candidate_cannot_change_the_skipped_test_at_all(project, state):
    """A changed test file is refused before any run: the skip trust rule never sees it
    (its own refusal of changed files is covered in tests/test_skip_evidence.py)."""
    base = _base(project)
    edited = "import pytest\ndef test_env_case():\n    pytest.skip('needs macOS sandbox-exec')\n"
    cand = candidate(project, base, {"src/calc.py": FIX, "tests/test_env.py": edited})
    code, out = verify(
        state, project, base, cand, allowed=("src/calc.py", "tests/test_env.py"), tests=(COMMAND,)
    )
    assert code != 0 and out["error"] == "PROTECTED_VERIFIER_INPUT"
