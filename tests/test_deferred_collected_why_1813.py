"""#1813 (from #1804 item 4): each concurrent check names its own reason, and a finished check
leaves nothing a later one can read; both asserted through behaviour, not module internals."""

import threading
from types import SimpleNamespace

import pytest

from cairnwork import deferred_collected
from cairnwork.collect_reader import Collected
from cairnwork.deferred_tests import TestCommitRejected as Rejected

PATH = "tests/test_regression.py"
REASON = {"t1": "collect-timeout", "t2": "collect-over-cap"}


def _stub(monkeypatch, barrier=None, base_barrier=None):
    monkeypatch.setattr(deferred_collected, "_exists", lambda *a: True)

    def verifiers(ctx, commit, specs, run_id):
        if commit == "boom":
            raise RuntimeError("x")
        return (
            SimpleNamespace(log_path="/nonexistent/x/log", commit=commit, files=specs[0].targets),
        )

    def read(run):
        if barrier is not None and run.commit in REASON:
            barrier.wait(timeout=10)  # both checks are mid-flight, with the same run id
        if run.commit == "b":
            if base_barrier is not None:  # both have recorded a reason, neither has read it yet
                base_barrier.wait(timeout=10)
            return Collected()
        return Collected(unusable=REASON.get(run.commit, "collect-timeout"))

    monkeypatch.setattr(deferred_collected, "run_verifiers", verifiers)
    monkeypatch.setattr(deferred_collected, "read_collected", read)


def _go(commit):
    task = SimpleNamespace(test_commit=commit, base_commit="b")
    deferred_collected.check(None, task, (PATH,), set(), "same-run-id")


def test_two_overlapping_checks_each_name_their_own_reason(monkeypatch):
    _stub(monkeypatch, threading.Barrier(2), threading.Barrier(2))
    got: dict[str, str] = {}

    def work(commit):
        try:
            _go(commit)
        except BaseException as err:  # noqa: BLE001
            got[commit] = f"{type(err).__name__} {err}"

    threads = [threading.Thread(target=work, args=(c,)) for c in REASON]
    [t.start() for t in threads]
    [t.join(timeout=30) for t in threads]
    assert set(got) == set(REASON)
    for commit, reason in REASON.items():
        other = next(v for k, v in REASON.items() if k != commit)
        assert reason in got[commit] and other not in got[commit]


def test_a_finished_check_leaves_no_reason_for_the_next_one(monkeypatch):
    _stub(monkeypatch)
    with pytest.raises(Rejected) as first:
        _go("t1")
    assert "collect-timeout" in str(first.value)
    with pytest.raises(Rejected) as second:  # a fault, not a timeout: no stale reason may show
        _go("boom")
    assert "collect-fault" in str(second.value) and "collect-timeout" not in str(second.value)
