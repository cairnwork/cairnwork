"""#244 (CI on a stock Linux runner): long-lived processes of the owner whose environment cannot
be read made every sweep UNREADABLE_PROCESSES. One that the kernel shows started before the
attempt was dispatched cannot carry the attempt's marker (set at spawn), so it does not hold
the fence; an unknown start time, or one within START_MARGIN of the attempt, still does.
Fake /proc and fake start times; one real start-time read on this host."""

import os
import subprocess
import sys
import time

import pytest
from test_sweep_result import FAKE, ME, _table

import cairnwork.confined as confined
from cairnwork import proc_start, recovery, takeover
from cairnwork.confined import START_MARGIN, sweep_marked
from cairnwork.journal import Journal, dispatched_at
from cairnwork.ownership import OWNER, OwnershipLedger

SINCE = 1_800_000_000.0  # the attempt's DISPATCHED time in these fakes
HIDDEN = (FAKE, FAKE, ME, None)


def _starts(monkeypatch, **by_pid):
    monkeypatch.setattr(proc_start, "start_times", lambda pids: {p: by_pid[str(p)] for p in pids
                                                                 if str(p) in by_pid})  # fmt: skip


@pytest.mark.parametrize(
    ("start", "counted"),
    [
        (SINCE - START_MARGIN - 1, False),  # provably older than the attempt: not its own
        (SINCE - START_MARGIN + 1, True),  # within the clock-skew margin: may be its own
        (SINCE + 10, True),  # started with or after the attempt
        (None, True),  # start time unknown
    ],
)
def test_a_hidden_process_older_than_the_attempt_does_not_hold_the_fence(
    monkeypatch, start, counted
):
    _table(monkeypatch, [HIDDEN])
    _starts(monkeypatch, **({} if start is None else {str(FAKE): start}))
    found = sweep_marked("CAIRNWORK_ATTEMPT", "a1", since=SINCE)
    assert (found.unreadable == 1) is counted and found.gone is not counted
    assert sweep_marked("CAIRNWORK_ATTEMPT", "a1").unreadable == 1  # since unknown: strict


def test_a_marked_process_is_killed_whatever_its_start_time(monkeypatch):
    killed = _table(monkeypatch, [(FAKE, FAKE, ME, {"CAIRNWORK_ATTEMPT=a1"})])
    _starts(monkeypatch, **{str(FAKE): SINCE - 3600})
    assert sweep_marked("CAIRNWORK_ATTEMPT", "a1", since=SINCE).killed == 1 and killed == [FAKE]


def _fake_proc(root, btime=1_700_000_000, rows=()):
    (root / "stat").write_text(f"cpu  1 2 3\nbtime {btime}\nprocesses 9\n")
    for pid, ticks in rows:
        (root / str(pid)).mkdir()
        fields = ["S", "1", str(pid)] + ["0"] * 16 + [str(ticks), "0", "0"]
        (root / str(pid) / "stat").write_text(f"{pid} (a b) c) " + " ".join(fields) + "\n")


def test_linux_start_times_are_btime_plus_ticks(tmp_path):
    tick = os.sysconf("SC_CLK_TCK")
    _fake_proc(tmp_path, rows=[(10, 5 * tick), (11, "x")])
    found = proc_start.linux_start_times([10, 11, 12], str(tmp_path))
    assert found == {10: 1_700_000_005.0}  # 11 unparsable, 12 gone: unknown, so counted
    (tmp_path / "stat").write_text("cpu 1\n")  # no btime: nothing is known
    assert proc_start.linux_start_times([10], str(tmp_path)) == {}


def test_real_start_times_from_this_kernel():
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        now = time.time()
        found = proc_start.start_times([os.getpid(), child.pid])
        assert found[child.pid] == pytest.approx(now, abs=START_MARGIN)
        assert found[os.getpid()] <= found[child.pid]
        assert proc_start.start_times([4_000_000_123]) == {}  # no such process: unknown
    finally:
        child.kill()
        child.wait()


def test_a_real_process_started_before_the_attempt_is_not_its_own(monkeypatch):
    """A real process, started before the attempt; only its environment read is faked hidden
    (making one hide it needs another account). Its start time comes from the kernel."""
    old = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        time.sleep(START_MARGIN + 1.5)
        since = time.time()
        _table(monkeypatch, [(old.pid, old.pid, ME, None)])
        assert sweep_marked("CAIRNWORK_ATTEMPT", "a1", since=since).gone
        assert not sweep_marked("CAIRNWORK_ATTEMPT", "a1", since=since - 60).gone
    finally:
        old.kill()
        old.wait()


def test_recovery_and_the_takeover_pass_the_dispatch_time(tmp_path, monkeypatch):
    ledger = OwnershipLedger(tmp_path / "own.sqlite")
    task_id = ledger.bind("k1", {"a": 1}, OWNER, "card-k1").record.task_id
    ledger.set_status(task_id, "DISPATCHED")
    journal = Journal(tmp_path / "journal.sqlite")
    journal.record(task_id, "a1", "DISPATCHED", str(tmp_path / "runs"))
    when = dispatched_at(journal.entries(task_id))
    assert when is not None and abs(when - time.time()) < 60
    seen = []

    def sweep(var, value, **kw):
        seen.append(kw.get("since"))
        return confined.Sweep(True, unreadable=1)

    monkeypatch.setattr(recovery, "sweep_marked", sweep)
    monkeypatch.setattr(takeover, "sweep_marked", sweep)
    monkeypatch.setattr(confined, "CONFIRM_WAIT", 0)
    from cairnwork.notary import Notary

    recovery.recover(ledger=ledger, notary=Notary(tmp_path / "notary.sqlite"), journal=journal,
                     run_root=tmp_path / "runs")  # fmt: skip
    assert ledger.unswept_attempt(task_id) is not None
    with pytest.raises(Exception, match="UNREADABLE_PROCESSES"):
        takeover.supersede(ledger, task_id, journal=journal)
    assert set(seen) == {when}
    seen.clear()
    with pytest.raises(Exception, match="UNREADABLE_PROCESSES"):
        takeover.supersede(ledger, task_id)  # no journal: start unknown, the strict rule
    assert set(seen) == {None}
