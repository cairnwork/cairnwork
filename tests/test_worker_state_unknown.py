"""#244: a worker whose state after launch is unknown keeps its attempt fenced.

- A dispatcher killed by a signal that is not our timeout may have launched a run whose receipt
  it never wrote: the attempt is fenced (WorkerMayLive), however its receipts read (lines lost,
  no launch receipt, an untrustworthy one, or an earlier attempt's receipts complete). A
  dispatcher that ended on its own, and our timeout, keep their own paths.
- An interrupt that is not an Exception (KeyboardInterrupt, SystemExit) leaves the task
  DISPATCHED, as a crash does, so only recovery settles it (sweep, then fence); it is never
  FAILED unmarked, which a supersede would take over without a sweep.
- A worker that fails outright is still FAILED with no mark.

Pure fakes only: no process is started, signalled or swept. Every sweep entry point fails the
test if it is reached, except where a fake sweep answer is installed."""

import json
import os
import types

import pytest
from agy_receipt_lines import RUN_TAG, WORKER, cleanup, launch, status, sweep
from test_pipeline import EditWorker, task
from test_recovery import project as project  # noqa: F401 - fixture
from test_recovery import settle, stores

import cairnwork.pipeline as pipeline
import cairnwork.recovery as recovery
from cairnwork import confined, takeover
from cairnwork.confined import UNLISTED, Sweep
from cairnwork.ownership import InvalidStatusTransition, TakeoverRefused
from cairnwork.pipeline import run_task
from cairnwork.unswept import Mark, WorkerMayLive
from cairnwork.workers import agy, agy_dispatch
from cairnwork.workers.agy_dispatch import AgyWorkerAccountFailed

ROOT = f"{agy_dispatch.WORKER_RUNS}/{RUN_TAG}"
BUSY = Sweep(True, account=1)  # the worker account still runs something


def _never(name):
    def called(*_a, **_k):
        raise AssertionError(f"{name} was reached")

    return called


@pytest.fixture(autouse=True)
def no_real_sweep(monkeypatch):
    for owner, name in (
        (agy_dispatch, "sweep_worker_run"),
        (confined, "sweep_marked"),
        (recovery, "sweep_marked"),
        (takeover, "sweep_marked"),
        (takeover, "_swept"),
    ):
        monkeypatch.setattr(owner, name, _never(f"{owner.__name__}.{name}"))
    monkeypatch.setattr(confined, "CONFIRM_WAIT", 0)


class _Reader:
    def __init__(self, lines, dropped=False):
        self._lines, self.unknown, self.dropped = [x.rstrip("\n") for x in lines], False, dropped

    def lines(self):
        return list(self._lines)


def _settle(tmp_path, lines, code, dropped=False, timed_out=None):
    dispatch = types.SimpleNamespace(run_as_user=WORKER)
    process = types.SimpleNamespace(returncode=code)
    log = tmp_path / "a1.agy.log"
    reader = _Reader(lines, dropped)
    return agy._settle_worker_run(dispatch, reader, process, False, timed_out, log)


@pytest.mark.parametrize(
    ("lines", "dropped"),
    [
        ([], True),  # every line lost, then killed by a signal not ours
        ([status("running")], True),  # some lines kept, none of them a launch
        ([launch(ROOT), launch(ROOT)], False),  # a launch receipt twice
        ([launch(ROOT, user="james")], False),  # a launch naming another account
        ([], False),  # no launch receipt at all (fenced before too)
    ],
    ids=["all-lost", "some-lost", "duplicate", "other-account", "none"],
)
def test_a_killed_dispatcher_with_unknown_launches_is_fenced(tmp_path, lines, dropped):
    with pytest.raises(WorkerMayLive) as raised:
        _settle(tmp_path, lines, -9, dropped)
    assert raised.value.account == WORKER
    assert str(raised.value).startswith("WORKER_SWEEP_UNCLEAN:")
    assert json.loads((tmp_path / "a1.agy.worker.json").read_text())["isolated"] is False


# attempt 1 launched, swept clean and removed (its root is gone, so nothing is swept again)
EARLIER = {
    "failed": [launch(ROOT), sweep(), cleanup(failure="AGY_PROVIDER_FAILED:quota")],
    "clean": [launch(ROOT), sweep(), cleanup()],
}


@pytest.mark.parametrize("earlier", sorted(EARLIER))
def test_a_dispatcher_killed_after_complete_receipts_is_fenced(tmp_path, earlier):
    """Killed by a signal not ours after attempt 1's receipts were whole: attempt 2 may have
    launched before its WORKER_LAUNCH line was written, so the receipts cannot clear it."""
    assert not os.path.lexists(ROOT)
    with pytest.raises(WorkerMayLive, match="KILLED_BY_SIGNAL: 9") as raised:
        _settle(tmp_path, EARLIER[earlier], -9)
    assert raised.value.account == WORKER
    record = json.loads((tmp_path / "a1.agy.worker.json").read_text())
    assert any(p.startswith("KILLED_BY_SIGNAL") for p in record["problems"])


@pytest.mark.parametrize("earlier", sorted(EARLIER))
def test_our_timeout_after_complete_receipts_keeps_its_own_path(tmp_path, earlier):
    assert _settle(tmp_path, EARLIER[earlier], -9, timed_out=TimeoutError()) is None


@pytest.mark.parametrize(
    ("lines", "dropped"),
    [([], True), ([launch(ROOT), launch(ROOT)], False), (EARLIER["failed"], False)],
)
def test_a_dispatcher_that_ended_on_its_own_stays_an_account_failure(tmp_path, lines, dropped):
    with pytest.raises(AgyWorkerAccountFailed) as raised:
        _settle(tmp_path, lines, 1, dropped)
    assert not isinstance(raised.value, WorkerMayLive)


class _SettlesKilled(EditWorker):
    """A worker that ends the way a killed dispatcher with lost lines does."""

    run_as = WORKER

    def run(self, workdir, task):
        _settle(workdir.parent, [], -9, dropped=True)


class _Interrupted(EditWorker):
    run_as = WORKER

    def __init__(self, error):
        super().__init__({})
        self.error = error

    def run(self, workdir, task):
        raise self.error


class _MayLive(EditWorker):
    run_as = WORKER

    def run(self, workdir, task):
        raise WorkerMayLive("WORKER_SWEEP_UNCLEAN: x", account=WORKER)


class _Fails(EditWorker):
    def run(self, workdir, task):
        raise RuntimeError("model unavailable")


def _run(project, tmp_path, monkeypatch, worker):  # noqa: F811
    monkeypatch.setattr(pipeline, "precheck", lambda *_a: None)
    monkeypatch.setattr(pipeline, "base_env_check", lambda *_a, **_k: None)
    ledger, notary, journal = stores(tmp_path)
    return run_task(
        task(project), ledger=ledger, notary=notary, journal=journal, worker=worker,
        run_root=tmp_path / "runs",
    )  # fmt: skip


def _fake_sweeps(monkeypatch, answer):
    calls = []
    monkeypatch.setattr(
        takeover, "_swept", lambda a, account, _m: calls.append((a, account)) or answer
    )
    return calls


def _phases(tmp_path, task_id):
    return [e.phase for e in stores(tmp_path)[2].entries(task_id)]


@pytest.mark.parametrize("worker", [_SettlesKilled({}), _MayLive({})], ids=["killed", "may-live"])
def test_a_run_that_may_have_left_a_worker_is_fenced_and_held(
    project, tmp_path, monkeypatch, worker
):  # noqa: F811
    result = _run(project, tmp_path, monkeypatch, worker)
    ledger, _, journal = stores(tmp_path)
    assert result.status == "FAILED" and result.reason.startswith("WORKER_FAILED")
    assert ledger.unswept_attempt(result.task_id) == Mark(result.attempt_id, WORKER, "")
    calls = _fake_sweeps(monkeypatch, BUSY)
    with pytest.raises(TakeoverRefused, match="WORKER_ACCOUNT_BUSY"):
        takeover.supersede(ledger, result.task_id, journal=journal)
    assert calls == [(result.attempt_id, WORKER)]
    assert ledger.get(task_id=result.task_id).status == "FAILED"


@pytest.mark.parametrize("error", [KeyboardInterrupt, SystemExit(1)], ids=["interrupt", "exit"])
def test_an_interrupt_during_the_worker_leaves_the_task_to_recovery(
    project,
    tmp_path,
    monkeypatch,
    error,  # noqa: F811
):
    with pytest.raises(BaseException) as raised:
        _run(project, tmp_path, monkeypatch, _Interrupted(error))
    assert not isinstance(raised.value, Exception)
    ledger, _, journal = stores(tmp_path)
    (held,) = ledger.in_flight()
    assert held.status == "DISPATCHED" and "FINISHED" not in _phases(tmp_path, held.task_id)
    with pytest.raises(InvalidStatusTransition):  # no takeover of a task still in flight
        takeover.supersede(ledger, held.task_id, journal=journal)
    monkeypatch.setattr(recovery, "sweep_marked", lambda *_a, **_k: UNLISTED)
    (decision,), _ = settle(tmp_path)
    assert decision.new_status == "FAILED" and decision.sweep_unavailable
    assert ledger.unswept_attempt(held.task_id) == Mark(decision.attempt_id, WORKER, "")
    calls = _fake_sweeps(monkeypatch, BUSY)
    with pytest.raises(TakeoverRefused):
        takeover.supersede(ledger, held.task_id, journal=journal)
    assert calls == [(decision.attempt_id, WORKER)]


def test_a_worker_that_fails_outright_is_failed_and_not_fenced(project, tmp_path, monkeypatch):  # noqa: F811
    result = _run(project, tmp_path, monkeypatch, _Fails({}))
    ledger, _, journal = stores(tmp_path)
    assert result.status == "FAILED" and ledger.unswept_attempt(result.task_id) is None
    assert _phases(tmp_path, result.task_id)[-1] == "FINISHED"
    assert takeover.supersede(ledger, result.task_id, journal=journal).status == "SUPERSEDED"
