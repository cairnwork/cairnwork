"""Issue #1137 point 2: exercise failure-evidence wiring through verify and worker judging."""

import pytest
from conftest import git
from test_deferred_tests import CHECK
from test_uncomparable_paths import run_cli
from verify_helpers import FIX, candidate, verify

pytestmark = pytest.mark.sandbox


def test_verify_run_json_always_contains_failure_evidence(project, state):
    """The CLI response must retain the record-only evidence field, even when empty."""
    base = git(project, "rev-parse", "main")
    cand = candidate(project, base, {"src/calc.py": FIX})
    code, out = verify(state, project, base, cand, tests=(" ".join(CHECK[0][1]),))
    assert code in (0, 2)
    assert "failure_evidence" in out
    assert isinstance(out["failure_evidence"], list)


def test_uncomparable_teeth_check_keeps_failure_evidence(project, state):
    """A deferred-test collection error is UNCOMPARABLE and carries the base failure record."""
    tests = "import new_name_1137\ndef test_it():\n    assert new_name_1137.value == 1\n"
    code, out = run_cli(project, state, tests, {"src/calc.py": FIX})
    assert code == 2 and out["status"] == "UNCOMPARABLE"
    assert out["uncomparable"]["code"] == "BASE_COLLECTION_ERROR"
    assert out["failure_evidence"]
    assert out["failure_evidence"][0]["failed_count"] >= 1


def test_verify_capture_without_account_or_test_commit_withholds_untrusted_text(project, state):
    """The ordinary verify path calls capture_for_task with no account and no hidden test commit."""
    base = git(project, "rev-parse", "main")
    cand = candidate(project, base, {"src/calc.py": "def add(a, b):\n    return a - b\n"})
    code, out = verify(state, project, base, cand, tests=(" ".join(CHECK[0][1]),))
    assert code == 2 and out["status"] == "FAILED"
    assert out["failure_evidence"]
    assert all(row["log_tail_withheld"] is True for row in out["failure_evidence"])
    assert all(
        row["failed_nodeids"] == [] and row["log_tail"] == "" for row in out["failure_evidence"]
    )
