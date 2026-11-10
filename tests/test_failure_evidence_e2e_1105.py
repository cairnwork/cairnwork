"""Issue #1105 point 1, end to end (run_task, real sandbox): a FAILED result names the failing
test and carries its log tail, and breaking the evidence capture changes nothing in the verdict."""

import pytest
from conftest import commit_change, git
from test_p2p_nodes import CALC, CALC_TESTS, CONFTEST, MUL_TEST, _run

from cairnwork import failure_evidence

pytestmark = pytest.mark.sandbox


def _failing(repo, tmp_path):
    tmp_path.mkdir(exist_ok=True)
    commit_change(
        repo, {"src/calc.py": CALC, "tests/conftest.py": CONFTEST, "tests/test_calc.py": CALC_TESTS}
    )
    check = (("pytest", ("python3", "-m", "pytest", "-q", "tests/test_calc.py::test_mul")),)
    wrong = CALC + "def mul(a, b):\n    return a + b\n"  # 2 + 3 != 6
    return _run(repo, tmp_path, ("src/calc.py",), {"src/calc.py": wrong},
                {"tests/test_calc.py": CALC_TESTS + MUL_TEST}, check)  # fmt: skip


def test_a_failed_result_names_the_failing_test_and_keeps_its_log_tail(repo, tmp_path):
    result = _failing(repo, tmp_path)
    assert result.status == "FAILED" and not result.verified
    failed = [e for e in result.failure_evidence if e["failed_nodeids"]]
    assert failed, result.failure_evidence
    assert "tests/test_calc.py::test_mul" in failed[0]["failed_nodeids"]
    assert "test_mul" in failed[0]["log_tail"] and not failed[0]["log_tail_withheld"]


def test_a_broken_evidence_capture_leaves_the_verdict_unchanged(repo, tmp_path, monkeypatch):
    good = _failing(repo, tmp_path / "a")
    other = tmp_path / "b" / "repo"  # a fresh repo, so the same candidate commits again
    other.mkdir(parents=True)
    git(other, "init", "-q", "-b", "main", "--template=")
    git(other, "config", "user.email", "test@example.invalid")
    git(other, "config", "user.name", "test")
    git(other, "commit", "-q", "--allow-empty", "-m", "base")

    def broken(*args, **kwargs):
        raise RuntimeError("diagnostics broke")

    monkeypatch.setattr(failure_evidence, "capture", broken)
    bad = _failing(other, tmp_path / "b")
    assert (bad.status, bad.verified, bad.reason) == (good.status, good.verified, good.reason)
    assert bad.failure_evidence and all(e["log_tail_unavailable"] for e in bad.failure_evidence)
