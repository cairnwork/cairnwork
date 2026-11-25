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


def test_the_probe_runs_on_the_base_commit_not_the_test_commit(monkeypatch):
    """A fake that tells commits apart: only the base reads. A probe run on the test commit would
    see no record too and let the failed run pass (review5's surviving mutant)."""
    seen = []

    def by_commit(ctx, commit, files, run_id):
        seen.append(commit)
        return Collected() if commit == "b" else None

    monkeypatch.setattr(deferred_collected, "_collect", by_commit)
    monkeypatch.setattr(deferred_collected, "_exists", lambda *a: True)
    task = SimpleNamespace(test_commit="t", base_commit="b")
    with pytest.raises(Rejected) as err:
        _check(task, set())
    assert err.value.code == "DEFERRED_TESTS_COLLECT_FAILED"
    assert seen == ["t", "b"]


def test_a_run_that_is_broken_on_the_base_probe_too_keeps_the_static_rule(monkeypatch):
    """#804: a table plugin that fails to load breaks every run; the base run says UNCOMPARABLE."""
    task = _fake(monkeypatch, None, None)
    _check(task, {f"{PATH}::test_x"})


def test_all_new_files_are_refused_when_a_base_test_file_collects(monkeypatch):
    task = _fake(monkeypatch, None, Collected())
    monkeypatch.setattr(deferred_collected, "_exists", lambda *a: False)  # every file is new
    monkeypatch.setattr(deferred_collected, "_probe_files", lambda *a: ("tests/test_old.py",))
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


@pytest.mark.parametrize(("count", "batches"), [(50, [50]), (51, [50, 1])])
def test_batch_boundary_at_the_file_cap(monkeypatch, count, batches):
    calls = []

    def fake(ctx, commit, files, run_id):
        calls.append(len(files))
        return Collected()

    monkeypatch.setattr(deferred_collected, "_collect", fake)
    got = deferred_collected._collect_all(None, "c", tuple(f"t{i}.py" for i in range(count)), "r")
    assert got is not None
    assert calls == batches


def test_one_unusable_batch_makes_the_whole_collect_unusable(monkeypatch):
    def fake(ctx, commit, files, run_id):
        return (
            None if "t50.py" in files else Collected()
        )  # the later batch fails, the first is fine

    monkeypatch.setattr(deferred_collected, "_collect", fake)
    monkeypatch.setattr(deferred_collected, "_exists", lambda *a: False)
    monkeypatch.setattr(deferred_collected, "_probe_files", lambda *a: ("tests/test_old.py",))
    names = tuple(f"t{i}.py" for i in range(deferred_collected.MAX_FILES + 1))
    assert deferred_collected._collect_all(None, "c", names, "r") is None
    task = SimpleNamespace(test_commit="t", base_commit="b")
    with pytest.raises(Rejected) as err:
        deferred_collected.check(None, task, names, set(), "r")
    assert err.value.code == "DEFERRED_TESTS_COLLECT_FAILED"


@sandbox
def test_a_hidden_test_in_a_new_file_cannot_hide_behind_a_failed_collect_run(
    project, tmp_path, monkeypatch
):
    real = deferred_collected.run_verifiers

    calls = []

    def broken(ctx, commit, *a, **k):
        calls.append(commit)
        if len(calls) == 1:  # only the test commit's run fails; the base probe after it reads
            raise OSError("collect-only run failed")
        return real(ctx, commit, *a, **k)

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


@pytest.mark.parametrize("why", ["collect-over-cap", "collect-timeout", "collect-not-recorded"])
def test_a_run_without_a_usable_record_names_the_reason(monkeypatch, caplog, why):
    """#1792: the unusable reason was lost; now the log and the message carry it (code same)."""
    monkeypatch.setattr(deferred_collected, "_exists", lambda *a: True)

    def verifiers(ctx, commit, specs, run_id):
        return (SimpleNamespace(log_path="/nonexistent/x/log", commit=commit),)

    monkeypatch.setattr(deferred_collected, "run_verifiers", verifiers)
    monkeypatch.setattr(
        deferred_collected,
        "read_collected",
        lambda run: Collected(unusable=why) if run.commit == "t" else NONE,
    )
    task = SimpleNamespace(test_commit="t", base_commit="b")
    with caplog.at_level("WARNING", logger=deferred_collected.LOG.name):
        with pytest.raises(Rejected) as err:
            _check(task, set())
    assert err.value.code == "DEFERRED_TESTS_COLLECT_FAILED"
    assert why in str(err.value) and why in caplog.text


def test_a_large_but_legal_collect_still_passes(monkeypatch):
    ids = tuple(f"{PATH}::test_{n}" for n in range(5000))
    monkeypatch.setattr(deferred_collected, "_exists", lambda *a: True)
    monkeypatch.setattr(
        deferred_collected,
        "run_verifiers",
        lambda *a: (SimpleNamespace(log_path="/nonexistent/x/log"),),
    )
    monkeypatch.setattr(deferred_collected, "read_collected", lambda run: Collected(ids))
    _check(SimpleNamespace(test_commit="t", base_commit="b"), set())


@pytest.fixture(autouse=True)
def _fresh_reasons():
    """Tests that call ``_collect`` directly leave keys; start every test from an empty dict."""
    deferred_collected._WHY.clear()


def _stub(monkeypatch, usable_for):
    """Real _collect/_collect_all over stubbed runs; read_collected is unusable unless the commit
    is in ``usable_for``; a commit named 'boom' makes run_verifiers raise."""
    monkeypatch.setattr(deferred_collected, "_exists", lambda *a: True)

    def verifiers(ctx, commit, specs, run_id):
        if commit == "boom":
            raise RuntimeError("x")
        return (
            SimpleNamespace(log_path="/nonexistent/x/log", commit=commit, files=specs[0].targets),
        )

    def read(run):
        if run.commit in usable_for:
            return NONE
        return Collected(unusable="collect-timeout" if len(run.files) > 1 else "collect-over-cap")

    monkeypatch.setattr(deferred_collected, "run_verifiers", verifiers)
    monkeypatch.setattr(deferred_collected, "read_collected", read)


def _go(task, files=(PATH,)):
    deferred_collected.check(None, task, files, set(), "r")


def test_nothing_is_left_behind_when_both_runs_fail(monkeypatch):
    _stub(monkeypatch, usable_for=())  # the test commit and the base probe both fail
    _go(SimpleNamespace(test_commit="t", base_commit="b"))  # returns (UNCOMPARABLE path)
    assert deferred_collected._WHY == {}


def test_nothing_is_left_behind_after_a_refusal_or_a_fault(monkeypatch):
    _stub(monkeypatch, usable_for=("b",))
    with pytest.raises(Rejected):
        _go(SimpleNamespace(test_commit="t", base_commit="b"))
    assert deferred_collected._WHY == {}
    with pytest.raises(Rejected) as err:  # a fault in the run itself, named collect-fault
        _go(SimpleNamespace(test_commit="boom", base_commit="b"))
    assert "collect-fault" in str(err.value) and deferred_collected._WHY == {}


def test_a_many_batch_failure_leaves_nothing_and_names_the_first_failing_batch(monkeypatch):
    _stub(monkeypatch, usable_for=("b",))
    files = tuple(f"tests/test_f{i}.py" for i in range(deferred_collected.MAX_FILES + 1))
    with pytest.raises(Rejected) as err:
        _go(SimpleNamespace(test_commit="t", base_commit="b"), files)
    assert "collect-timeout" in str(err.value)  # batch 0 holds 50 files: the first failing batch
    assert deferred_collected._WHY == {}
