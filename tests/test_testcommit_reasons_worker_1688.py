"""#1688: the worker path (``run_task``) too. A hidden run's refusal reaches
``result.reason`` as the code and a count through each of the three exits in ``pipeline``;
a visible run keeps ``str(exc)``."""

import pytest
from test_deferred_tests import REGRESSION, SeeingWorker, make_test_commit, run

from cairnwork import pipeline
from cairnwork.deferred_overlay import TestCommitRejected

MARK = "CANARY1688"
FIX = {"src/calc.py": "def add(a, b):\n    return a + b\n"}
WITHHELD = "{}: 1 refusal (detail withheld)"


def _boom(code, detail=MARK):
    def raiser(*_a, **_k):
        raise TestCommitRejected(code, detail)

    return raiser


@pytest.mark.parametrize(
    ("files", "code"),
    [
        ({f"tests/{MARK}dir/setup.cfg": "[x]\n"}, "TEST_COMMIT_SCOPE"),
        ({f"tests/test_{MARK}.py": f"def {MARK}(:\n"}, "TEST_COMMIT_UNPARSABLE"),
    ],
)
def test_real_refusals_through_the_worker_path_name_nothing(project, tmp_path, files, code):
    base, tests = make_test_commit(project, files)
    result = run(project, tmp_path, base, tests, SeeingWorker(FIX))
    assert result.status == "FAILED", result.reason
    assert result.reason == WITHHELD.format(code), result.reason
    assert MARK not in result.reason


def test_the_first_catch_site_withholds_the_detail(project, tmp_path, monkeypatch):
    base, tests = make_test_commit(project, {"tests/test_regression.py": REGRESSION})
    monkeypatch.setattr(pipeline, "tests_unused", _boom("TEST_COMMIT_ALREADY_EXPOSED"))
    result = run(project, tmp_path, base, tests, SeeingWorker(FIX))
    assert result.reason == WITHHELD.format("TEST_COMMIT_ALREADY_EXPOSED"), result.reason
    assert MARK not in result.reason


def test_the_infra_error_exit_withholds_the_detail(project, tmp_path, monkeypatch):
    base, tests = make_test_commit(project, {"tests/test_regression.py": REGRESSION})
    monkeypatch.setattr(pipeline, "judge_for", _boom("TEST_COMMIT_SCOPE"))
    result = run(project, tmp_path, base, tests, SeeingWorker(FIX))
    want = "INFRA_ERROR: TestCommitRejected: " + repr(WITHHELD.format("TEST_COMMIT_SCOPE"))
    assert result.reason == want, result.reason
    assert MARK not in result.reason


def test_a_visible_run_keeps_the_text_byte_for_byte(project, tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "judge_for", _boom("TEST_COMMIT_SCOPE"))
    base = run_base(project)
    result = run(project, tmp_path, base, None, SeeingWorker(FIX))
    assert f"TEST_COMMIT_SCOPE: {MARK}" in result.reason, result.reason


def run_base(project):
    return make_test_commit(project, {"tests/test_regression.py": REGRESSION})[0]
