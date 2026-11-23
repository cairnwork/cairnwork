"""#974: a test pytest collects as new that ``new_tests`` cannot see is refused, not required-to-run
by nobody. Each wrong shape sits beside a visible new test (the false-green case); the correct pairs
are the same moves written the way the reader sees them. Real collect-only runs, worker path."""

from types import SimpleNamespace
from typing import Any

import pytest
from test_deferred_tests import REGRESSION, SeeingWorker, make_test_commit, run
from verify_helpers import FIX

from cairnwork import deferred_collected
from cairnwork.collect_reader import Collected
from cairnwork.deferred_tests import TestCommitRejected as Rejected

sandbox = pytest.mark.sandbox
NONE = Collected()
PATH = "tests/test_regression.py"
HIDDEN = "    def test_h(self):\n        pass\n"
WRONG = {  # categories: a unittest base the reader cannot name, a default runTest
    "from-import-alias": "from unittest import TestCase as TC\nclass Hid(TC):\n" + HIDDEN,
    "attribute-base": "import unittest\nB = unittest.TestCase\nclass Hid(B):\n" + HIDDEN,
    "subscript-base": "import unittest\nbs = [unittest.TestCase]\nclass Hid(bs[0]):\n" + HIDDEN,
    "default-runtest": "import unittest\nclass Hid(unittest.TestCase):\n    def runTest(self):\n"
    "        pass\n",
}
RIGHT = {
    "plain-class": "class TestHid:\n" + HIDDEN,
    "direct-testcase": "import unittest\nclass Hid(unittest.TestCase):\n" + HIDDEN,
    "parametrized": "import pytest\n@pytest.mark.parametrize('n', [1, 2])\n"
    "def test_p(n):\n    pass\n",
}


def _run(project, tmp_path, extra):
    base, test_commit = make_test_commit(project, {PATH: REGRESSION + extra})
    return run(project, tmp_path, base, test_commit, SeeingWorker({"src/calc.py": FIX}),
               verifiers=(("pytest", ("python3", "-m", "pytest", "-q", PATH)),))  # fmt: skip


@sandbox
@pytest.mark.parametrize("shape", WRONG)
def test_a_new_collected_test_the_reader_cannot_see_is_refused(project, tmp_path, shape):
    result = _run(project, tmp_path, WRONG[shape])
    assert result.status == "FAILED", result.reason
    assert result.reason.startswith("DEFERRED_TESTS_UNREADABLE"), result.reason
    assert "Hid" not in result.reason and "test_h" not in result.reason


@sandbox
@pytest.mark.parametrize("shape", RIGHT)
def test_the_same_move_written_where_the_reader_sees_it_completes(project, tmp_path, shape):
    result = _run(project, tmp_path, RIGHT[shape])
    assert result.status == "COMPLETED", result.reason


def _fake(monkeypatch, after: Any, before: Any = NONE):
    seq = iter([after, before])
    monkeypatch.setattr(deferred_collected, "_collect", lambda *a: next(seq))
    monkeypatch.setattr(deferred_collected, "_exists", lambda *a: True)
    return SimpleNamespace(test_commit="t", base_commit="b")


def _check(task, targets):
    deferred_collected.check(None, task, (PATH,), targets, "r")


def test_a_test_commit_run_with_no_record_is_refused_when_the_base_collected(monkeypatch):
    task = _fake(monkeypatch, None, Collected((f"{PATH}::test_old",)))
    with pytest.raises(Rejected) as err:
        _check(task, set())
    assert err.value.code == "DEFERRED_TESTS_COLLECT_FAILED"


def test_a_test_commit_run_with_no_record_is_refused_even_without_base_evidence(monkeypatch):
    task = _fake(monkeypatch, None, None)
    with pytest.raises(Rejected) as err:
        _check(task, {f"{PATH}::test_x"})
    assert err.value.code == "DEFERRED_TESTS_COLLECT_FAILED"
    task = _fake(monkeypatch, None)
    monkeypatch.setattr(deferred_collected, "_exists", lambda *a: False)  # every file is new
    with pytest.raises(Rejected) as err:
        _check(task, set())
    assert err.value.code == "DEFERRED_TESTS_COLLECT_FAILED"


def test_an_unreadable_base_run_leaves_the_old_ids_unknown_and_the_static_rule(monkeypatch):
    task = _fake(monkeypatch, Collected((f"{PATH}::test_a",)), None)
    _check(task, set())


def test_more_files_than_one_batch_are_collected_in_batches(monkeypatch):
    names = [f"tests/test_f{i}.py" for i in range(deferred_collected.MAX_FILES + 3)]
    calls = []

    def fake(ctx, commit, files, run_id):
        calls.append(len(files))
        return Collected(tuple(f"{f}::test_x" for f in files))

    monkeypatch.setattr(deferred_collected, "_collect", fake)
    monkeypatch.setattr(deferred_collected, "_exists", lambda *a: False)
    task = SimpleNamespace(test_commit="t", base_commit="b")
    with pytest.raises(Rejected) as err:  # a file past the first batch is still checked
        deferred_collected.check(None, task, tuple(names), set(), "r")
    assert err.value.code == "DEFERRED_TESTS_UNREADABLE"
    assert calls == [deferred_collected.MAX_FILES, 3]


@sandbox
def test_a_hidden_test_in_a_new_file_cannot_hide_behind_a_failed_collect_run(
    project, tmp_path, monkeypatch
):
    def broken(*a, **k):
        raise OSError("collect-only run failed")

    monkeypatch.setattr(deferred_collected, "run_verifiers", broken)
    result = _run(project, tmp_path, WRONG["attribute-base"])  # new file, no base evidence
    assert result.status == "FAILED", result.reason
    assert result.reason.startswith("DEFERRED_TESTS_COLLECT_FAILED"), result.reason


def test_a_file_whose_collector_failed_is_unknown_not_refused(monkeypatch):
    task = _fake(monkeypatch, Collected((), (PATH,)))
    _check(task, set())
    task = _fake(monkeypatch, Collected((f"{PATH}::test_a",)), Collected((), (PATH,)))
    _check(task, set())  # the base could not collect the file: its old ids are unknown


def test_an_unusable_collect_run_reads_as_nothing(monkeypatch, tmp_path):
    run = SimpleNamespace(log_path=tmp_path / "x" / "v.log")
    monkeypatch.setattr(deferred_collected, "run_verifiers", lambda *a, **k: (run,))
    monkeypatch.setattr(deferred_collected, "read_collected", lambda r: Collected(unusable="why"))
    assert deferred_collected._collect(None, "c", (PATH,), "r") is None
    monkeypatch.setattr(deferred_collected, "read_collected", lambda r: Collected())
    assert deferred_collected._collect(None, "c", (PATH,), "r") == Collected()


def test_an_id_the_base_already_collected_or_the_reader_sees_passes(monkeypatch):
    ids = (f"{PATH}::test_old", f"{PATH}::test_p[1]", f"{PATH}::test_p[2]", f"{PATH}::test_n")
    task = _fake(monkeypatch, Collected(ids), Collected((f"{PATH}::test_old",)))
    _check(task, {f"{PATH}::test_p", f"{PATH}::test_n"})


def test_a_new_id_the_reader_does_not_hold_is_refused_even_beside_a_seen_one(monkeypatch):
    ids = (f"{PATH}::test_n", f"{PATH}::Hid::test_h[1]")
    task = _fake(monkeypatch, Collected(ids), NONE)
    with pytest.raises(Rejected) as err:
        _check(task, {f"{PATH}::test_n"})
    assert err.value.code == "DEFERRED_TESTS_UNREADABLE"
    assert "Hid" not in str(err.value) and "test_h" not in str(err.value)  # no candidate text
    assert "1 new test" in str(err.value)
