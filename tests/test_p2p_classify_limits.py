"""The collected-test split runs under a strict wall-clock limit."""

from __future__ import annotations

import os
import sys

import pytest
from conftest import commit_change
from test_p2p_nodes import CALC, CALC_TESTS, CONFTEST, MUL_TEST, SUB_OFF, _run

from cairnwork import p2p_collect
from cairnwork.deferred_tests import TestCommitRejected as Rejected


def _stub(code: str, *args: str) -> list[str]:
    return [sys.executable, "-c", code, *args]


def test_normal_classification_preserves_removed_and_dropped_sets():
    side = p2p_collect.classify(
        {"t.py::test_gone", "t.py::test_kept", "t.py::test_hidden"},
        {"t.py::test_kept"},
        {"t.py"},
        lambda _: "def test_kept(): pass\nif False:\n    def test_hidden(): pass\n",
        lambda _: "def test_gone(): pass\ndef test_kept(): pass\ndef test_hidden(): pass\n",
    )
    assert side.removed == {"t.py::test_gone"}
    assert side.dropped == {"t.py::test_hidden"}


def test_slow_child_is_killed_and_refused(monkeypatch, tmp_path):
    pid_file = tmp_path / "pid"
    code = "import os,sys,time; open(sys.argv[1],'w').write(str(os.getpid())); time.sleep(30)"
    monkeypatch.setattr(p2p_collect, "_classify_argv", lambda: _stub(code, str(pid_file)))
    monkeypatch.setattr(p2p_collect, "CLASSIFY_TIMEOUT_SECONDS", 0.1)
    with pytest.raises(Rejected) as caught:
        p2p_collect.classify({"t.py::test_a"}, set(), set(), lambda _: "", lambda _: "")
    assert caught.value.code == "TEST_COMMIT_ANALYSIS_LIMIT"
    assert "time" in str(caught.value)
    child_pid = int(pid_file.read_text())
    with pytest.raises(ProcessLookupError):
        os.killpg(child_pid, 0)


def test_same_child_finishes_within_limit(monkeypatch):
    code = 'import time; time.sleep(.02); print(\'{"removed":[],"dropped":["t.py::test_a"]}\')'
    monkeypatch.setattr(p2p_collect, "_classify_argv", lambda: _stub(code))
    monkeypatch.setattr(p2p_collect, "CLASSIFY_TIMEOUT_SECONDS", 1)
    side = p2p_collect.classify({"t.py::test_a"}, set(), set(), lambda _: "", lambda _: "")
    assert side.removed == set() and side.dropped == {"t.py::test_a"}


def test_child_cannot_return_ids_outside_the_base(monkeypatch):
    code = 'print(\'{"removed":["t.py::extra"],"dropped":[]}\')'
    monkeypatch.setattr(p2p_collect, "_classify_argv", lambda: _stub(code))
    with pytest.raises(Rejected) as caught:
        p2p_collect.classify({"t.py::test_a"}, set(), set(), lambda _: "", lambda _: "")
    assert caught.value.code == "TEST_COMMIT_ANALYSIS_LIMIT"


@pytest.mark.sandbox
def test_pipeline_analysis_timeout_is_refused_without_verification(monkeypatch, repo, tmp_path):
    code = "import time; time.sleep(30)"
    monkeypatch.setattr(p2p_collect, "_classify_argv", lambda: _stub(code))
    monkeypatch.setattr(p2p_collect, "CLASSIFY_TIMEOUT_SECONDS", 0.1)
    commit_change(
        repo,
        {"src/calc.py": CALC, "tests/conftest.py": CONFTEST, "tests/test_calc.py": CALC_TESTS},
    )
    check = (("pytest", ("python3", "-m", "pytest", "-q", "tests/test_calc.py::test_mul")),)
    result = _run(
        repo,
        tmp_path,
        ("src/calc.py",),
        {"src/calc.py": SUB_OFF},
        {"tests/conftest.py": CONFTEST, "tests/test_calc.py": CALC + MUL_TEST},
        check,
    )
    assert result.status == "FAILED" and not result.verified
    assert "TEST_COMMIT_ANALYSIS_LIMIT" in result.reason
