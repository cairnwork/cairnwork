"""#1267: a supersede and the runner's dispatch of the same task have exactly one winner.

Both paths move the ownership row in one ``BEGIN IMMEDIATE`` compare-and-set (``set_status``), so
whichever commits first wins and the other is refused by TRANSITIONS. These tests pin both orders
and a real thread race, with a stated outcome for the loser. Test-only: no verdict logic changes.
"""

import threading

import pytest
from test_dispatch_once import CountingWorker, _run
from test_dispatch_race import ChangeAfterRead, _bind, _stores
from test_pipeline import project, task  # noqa: F401 - project is a fixture

import cairnwork.takeover as takeover
from cairnwork.ownership import InvalidStatusTransition

pytestmark = pytest.mark.sandbox


class SupersedeAfterDispatch:
    """Right after the runner's DISPATCHED move commits, a supersede is tried on the same task."""

    def __init__(self, ledger, refused):
        self._ledger, self._refused = ledger, refused

    def set_status(self, task_id, status, **kwargs):
        record = self._ledger.set_status(task_id, status, **kwargs)
        if status == "DISPATCHED":
            try:
                takeover.supersede(self._ledger, task_id)
            except InvalidStatusTransition as exc:
                self._refused.append(str(exc))
        return record

    def __getattr__(self, name):
        return getattr(self._ledger, name)


def test_a_supersede_landing_before_the_dispatch_wins(project, tmp_path):  # noqa: F811
    ledger, notary, journal = _stores(tmp_path)
    spec = task(project)
    task_id = _bind(ledger, spec)
    worker = CountingWorker()
    racing = ChangeAfterRead(ledger, "SUPERSEDED")
    result = _run(spec, task_id, racing, notary, journal, worker, tmp_path)
    assert (result.status, result.reason) == ("SUPERSEDED", "NOT_BOUND")
    assert worker.calls == 0 and ledger.get(task_id=task_id).status == "SUPERSEDED"


def test_a_supersede_landing_after_the_dispatch_is_refused(project, tmp_path):  # noqa: F811
    ledger, notary, journal = _stores(tmp_path)
    spec = task(project)
    task_id = _bind(ledger, spec)
    worker, refused = CountingWorker(), []
    result = _run(
        spec, task_id, SupersedeAfterDispatch(ledger, refused), notary, journal, worker, tmp_path
    )
    assert refused == ["INVALID_STATUS_TRANSITION: DISPATCHED -> SUPERSEDED"]
    assert worker.calls == 1 and result.reason != "NOT_BOUND"
    assert ledger.get(task_id=task_id).status != "SUPERSEDED"


@pytest.mark.parametrize("round_", range(12))
def test_a_real_thread_race_has_exactly_one_winner(project, tmp_path, round_):  # noqa: F811
    ledger, notary, journal = _stores(tmp_path)
    spec = task(project)
    task_id = _bind(ledger, spec)
    worker, start, seen = CountingWorker(), threading.Barrier(2, timeout=30), {}

    def runner():
        start.wait()
        seen["run"] = _run(spec, task_id, ledger, notary, journal, worker, tmp_path)

    def superseder():
        start.wait()
        try:
            seen["supersede"] = takeover.supersede(ledger, task_id).status
        except InvalidStatusTransition as exc:
            seen["supersede"] = str(exc)

    threads = [threading.Thread(target=runner), threading.Thread(target=superseder)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    if seen["supersede"] == "SUPERSEDED":  # supersede won: the runner never ran the worker
        assert (seen["run"].status, seen["run"].reason) == ("SUPERSEDED", "NOT_BOUND")
        assert worker.calls == 0 and ledger.get(task_id=task_id).status == "SUPERSEDED"
    else:  # dispatch won: the supersede is refused with the transition it tried
        assert seen["supersede"].endswith("DISPATCHED -> SUPERSEDED")
        assert worker.calls == 1 and seen["run"].reason != "NOT_BOUND"
        assert ledger.get(task_id=task_id).status != "SUPERSEDED"


class _GatedConnection:
    """Proxy a connection; each ``BEGIN`` first waits for the other side (or times out)."""

    def __init__(self, conn, gate):
        self._conn, self._gate = conn, gate

    def execute(self, sql, *args):
        if sql.strip().upper().startswith("BEGIN"):
            try:
                self._gate.wait(timeout=1.0)
            except threading.BrokenBarrierError:
                self._gate.reset()
        return self._conn.execute(sql, *args)

    def __getattr__(self, name):
        return getattr(self._conn, name)


def test_two_moves_that_start_together_cannot_both_write(project, tmp_path, monkeypatch):  # noqa: F811
    """Line both sides up before every transaction start, so a split check-and-update lets both in.

    With the single ``BEGIN IMMEDIATE`` the second side reads only after the first has committed
    and is refused, so one write lands. If the check and the update were separate transactions,
    both sides would pass the check on BOUND and meet again before the update: two writes land."""
    from cairnwork.ownership import OwnershipError, OwnershipLedger

    ledger, notary, journal = _stores(tmp_path)
    task_id = _bind(ledger, task(project))
    gate, wins = threading.Barrier(2), []
    real_connect = OwnershipLedger._connect
    monkeypatch.setattr(
        OwnershipLedger, "_connect", lambda self: _GatedConnection(real_connect(self), gate)
    )

    def mover(status):
        try:
            ledger.set_status(task_id, status, expected_from="BOUND")
            wins.append(status)
        except OwnershipError:
            pass

    threads = [threading.Thread(target=mover, args=(s,)) for s in ("DISPATCHED", "SUPERSEDED")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    monkeypatch.undo()
    assert len(wins) == 1
    assert ledger.get(task_id=task_id).status == wins[0]
