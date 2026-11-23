"""#1246 part 2: the classifier child has a memory limit; a hit is refused, never VERIFIED."""

from __future__ import annotations

import sys
import time

import pytest
from conftest import commit_change
from test_p2p_nodes import CALC, CALC_TESTS, CONFTEST, MUL_TEST, SUB_OFF, _run

from cairnwork import p2p_collect
from cairnwork.deferred_tests import TestCommitRejected as Rejected

MARK = "ZQ1246-UNIQUE-MARK"
_HOG = "x = bytearray(300 << 20); x[::4096] = b'1' * len(x[::4096]); import time; time.sleep(60)"
# a flat literal of a million elements: parsing it takes far more than the 40 MiB limit below
_BIG = f"# {MARK}\nA = [" + "0," * 1_000_000 + "]\ndef test_gone(): pass\n"


def test_the_classifier_has_a_memory_limit():
    assert 0 < p2p_collect.CLASSIFY_MEMORY_LIMIT <= 2 << 30


def test_real_child_over_the_limit_is_refused_and_leaks_nothing(monkeypatch):
    monkeypatch.setattr(p2p_collect, "CLASSIFY_MEMORY_LIMIT", 40 << 20)
    monkeypatch.setattr(p2p_collect, "CLASSIFY_TIMEOUT_SECONDS", 60)
    started = time.monotonic()
    with pytest.raises(Rejected) as caught:
        p2p_collect.classify(
            {"t.py::test_gone"},
            set(),
            {"t.py"},
            lambda _: _BIG,
            lambda _: _BIG,
            exposed=(),
        )
    assert caught.value.code == "TEST_COMMIT_ANALYSIS_LIMIT"
    assert MARK not in str(caught.value) and MARK not in caught.value.code
    assert time.monotonic() - started < 30


def test_a_hog_child_is_refused_before_the_time_limit(monkeypatch):
    monkeypatch.setattr(p2p_collect, "_classify_argv", lambda: [sys.executable, "-c", _HOG])
    monkeypatch.setattr(p2p_collect, "CLASSIFY_MEMORY_LIMIT", 100 << 20)
    monkeypatch.setattr(p2p_collect, "CLASSIFY_TIMEOUT_SECONDS", 60)
    started = time.monotonic()
    with pytest.raises(Rejected) as caught:
        p2p_collect.classify({"t.py::a"}, set(), set(), lambda _: "", lambda _: "", exposed=())
    assert caught.value.code == "TEST_COMMIT_ANALYSIS_LIMIT"
    assert time.monotonic() - started < 20


def test_ordinary_classification_is_unchanged_under_the_limit():
    side = p2p_collect.classify(
        {"t.py::test_gone", "t.py::test_kept"},
        {"t.py::test_kept"},
        {"t.py"},
        lambda _: "def test_kept(): pass\n",
        lambda _: "def test_gone(): pass\ndef test_kept(): pass\n",
        exposed=(),
    )
    assert side.removed == {"t.py::test_gone"} and not side.dropped


@pytest.mark.sandbox
def test_pipeline_memory_hit_is_refused_without_verification(monkeypatch, repo, tmp_path):
    monkeypatch.setattr(p2p_collect, "_classify_argv", lambda: [sys.executable, "-c", _HOG])
    monkeypatch.setattr(p2p_collect, "CLASSIFY_MEMORY_LIMIT", 100 << 20)
    monkeypatch.setattr(p2p_collect, "CLASSIFY_TIMEOUT_SECONDS", 60)
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
        {"tests/conftest.py": CONFTEST, "tests/test_calc.py": CALC + MUL_TEST + f"# {MARK}\n"},
        check,
    )
    assert result.status == "FAILED" and not result.verified
    assert "TEST_COMMIT_ANALYSIS_LIMIT" in result.reason
    assert MARK not in result.reason
