"""#244 (CI on a stock Linux runner): long-lived processes of the owner whose environment cannot
be read made every sweep UNREADABLE_PROCESSES. On Linux, one that the kernel shows started
before the attempt was dispatched cannot carry the attempt's marker (set at spawn), so it does
not hold the fence. Gemini round 3: only boot-relative times are compared (DISPATCHED journals
the boot id and CLOCK_BOOTTIME; /proc/<pid>/stat field 22), so stepping the wall clock changes
nothing; another boot, an old journal, an unreadable start time and macOS keep the strict rule.
Fake /proc and fake start times; real processes on this host."""

import os
import sqlite3
import subprocess
import sys
import time

import pytest
from test_sweep_result import FAKE, ME, _table

import cairnwork.confined as confined
from cairnwork import proc_start, recovery, takeover
from cairnwork.confined import START_MARGIN, sweep_marked
from cairnwork.journal import Journal, dispatch_mark
from cairnwork.ownership import OWNER, OwnershipLedger

BOOT = "0f1e2d3c-boot"
AT = 10_000.0  # seconds after boot the attempt was dispatched, in these fakes
MARK = f"{BOOT} {AT!r}"
HIDDEN = (FAKE, FAKE, ME, None)


@pytest.fixture
def linux(monkeypatch):
    monkeypatch.setattr(proc_start, "LINUX", True)


def _fake_proc(root, boot: str | None = BOOT, rows=()):
    """A /proc with a boot id and, per (pid, seconds after boot or text), a stat line whose
    command name holds ")", " " and "(" (field 2 may hold any)."""
    (root / "sys/kernel/random").mkdir(parents=True)
    if boot is not None:
        (root / proc_start.BOOT_ID).write_text(boot + "\n")
    tick = os.sysconf("SC_CLK_TCK")
    for pid, start in rows:
        ticks = str(int(start * tick)) if isinstance(start, float) else start
        (root / str(pid)).mkdir()
        fields = ["S", "1", str(pid)] + ["0"] * 16 + [ticks, "0", "0"]
        (root / str(pid) / "stat").write_text(f"{pid} (a) b (c) " + " ".join(fields) + "\n")
    return str(root)


def _kernel(monkeypatch, tmp_path, start):
    """sweep_marked's start-time reads go to a fake /proc with FAKE started at start."""
    proc = _fake_proc(tmp_path, rows=[] if start is None else [(FAKE, start)])
    real = proc_start.not_older
    monkeypatch.setattr(proc_start, "not_older", lambda p, m, g: real(p, m, g, proc))


@pytest.mark.parametrize(
    ("start", "counted"),
    [
        (AT - START_MARGIN - 1, False),  # provably older than the attempt: not its own
        (AT - START_MARGIN + 1, True),  # within the margin: may be its own
        (AT + 10, True),  # started with or after the attempt
        (None, True),  # start time unknown
    ],
)
def test_a_hidden_process_older_than_the_attempt_does_not_hold_the_fence(
    monkeypatch, tmp_path, linux, start, counted
):
    _table(monkeypatch, [HIDDEN])
    _kernel(monkeypatch, tmp_path, start)
    found = sweep_marked("CAIRNWORK_ATTEMPT", "a1", dispatched=MARK)
    assert (found.unreadable == 1) is counted and found.gone is not counted
    assert sweep_marked("CAIRNWORK_ATTEMPT", "a1").unreadable == 1  # no mark: strict


def test_a_marked_process_is_killed_whatever_its_start_time(monkeypatch, tmp_path, linux):
    killed = _table(monkeypatch, [(FAKE, FAKE, ME, {"CAIRNWORK_ATTEMPT=a1"})])
    _kernel(monkeypatch, tmp_path, AT - 3600)
    assert sweep_marked("CAIRNWORK_ATTEMPT", "a1", dispatched=MARK).killed == 1
    assert killed == [FAKE]


@pytest.mark.parametrize(
    "mark",
    ["", BOOT, f"{BOOT} x", f"{BOOT} nan", f"{BOOT} inf", f" {AT}", f"another-boot {AT}"],
)
def test_a_mark_not_from_this_boot_keeps_the_strict_rule(tmp_path, linux, mark):
    """Gemini #244 round 3 MUST-FIX: after a reboot the tick counts restart, so a mark from
    another boot (or one that cannot be read) says nothing about which process is older."""
    proc = _fake_proc(tmp_path, rows=[(10, 1.0)])
    assert proc_start.not_older([10], MARK, START_MARGIN, proc) == []  # this boot: old
    assert proc_start.not_older([10], mark, START_MARGIN, proc) == [10]


def test_an_unreadable_boot_id_keeps_the_strict_rule(tmp_path, linux):
    proc = _fake_proc(tmp_path, boot=None, rows=[(10, 1.0)])
    assert proc_start.not_older([10], MARK, START_MARGIN, proc) == [10]
    assert proc_start.boot_mark(proc) == ""


def test_off_linux_the_filter_is_off(monkeypatch, tmp_path):
    """macOS gives start times only on the wall clock and nothing there needs the filter."""
    proc = _fake_proc(tmp_path, rows=[(10, 1.0)])
    monkeypatch.setattr(proc_start, "LINUX", False)
    assert proc_start.boot_mark(proc) == ""
    assert proc_start.not_older([10], MARK, START_MARGIN, proc) == [10]


def test_the_stat_fields_are_counted_after_the_last_parenthesis(tmp_path):
    """Gemini #244 round 3: the command name "a) b (c" must not shift field 22."""
    tick = os.sysconf("SC_CLK_TCK")
    proc = _fake_proc(tmp_path, rows=[(10, 5.0), (11, "x")])
    assert proc_start.boot_start(10, proc) == int(5.0 * tick) / tick
    assert proc_start.boot_start(11, proc) is None  # unparsable: unknown, so counted
    assert proc_start.boot_start(12, proc) is None  # gone


def test_the_boot_mark_is_the_boot_id_and_the_boot_clock(monkeypatch, tmp_path, linux):
    proc = _fake_proc(tmp_path)
    monkeypatch.setattr(time, "CLOCK_BOOTTIME", 7, raising=False)
    real = time.clock_gettime
    monkeypatch.setattr(time, "clock_gettime", lambda clock: 123.5 if clock == 7 else real(clock))
    assert proc_start.boot_mark(proc) == f"{BOOT} 123.5"


def test_dispatched_journals_the_boot_mark_and_nothing_else_does(tmp_path, monkeypatch):
    monkeypatch.setattr(proc_start, "boot_mark", lambda: MARK)
    journal = Journal(tmp_path / "journal.sqlite")
    journal.record("t", "a", "DISPATCHED", "/runs")
    journal.record("t", "a", "WORKER_STARTED", "run_as=")
    entries = journal.entries("t")
    assert [e.boot for e in entries] == [MARK, ""] and dispatch_mark(entries) == MARK
    assert dispatch_mark([e for e in entries if e.phase != "DISPATCHED"]) == ""


@pytest.mark.parametrize("at", ["2026-10-25T10:00:00", "2026-10-25T10:00:00+00:00", "now"])
def test_a_journal_from_before_the_mark_keeps_the_strict_rule(tmp_path, at):
    """Gemini #244 round 3: the old DISPATCHED row gains an empty mark; its ``at`` (naive,
    aware or not a time at all) is never read for the filter."""
    path = tmp_path / "journal.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript("CREATE TABLE attempt_journal (seq INTEGER"
                       " PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, attempt_id TEXT NOT"
                       " NULL, phase TEXT NOT NULL CHECK (phase IN ('DISPATCHED', 'WORKER_STARTED',"
                       " 'CANDIDATE', 'TEST_SOURCE', 'VERIFYING', 'NOTARIZING', 'FINISHED')),"
                       " detail TEXT NOT NULL DEFAULT '', at TEXT NOT NULL)")  # fmt: skip
    conn.execute("INSERT INTO attempt_journal VALUES (1, 't', 'a', 'DISPATCHED', '/r', ?)", (at,))
    conn.commit()
    conn.close()
    journal = Journal(path)
    assert dispatch_mark(journal.entries("t")) == ""
    journal.record("t", "a", "FINISHED", "FAILED")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        sqlite3.connect(path).execute("DELETE FROM attempt_journal")


def _sleeper():
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])


@pytest.mark.skipif(not proc_start.LINUX, reason="the boot clock and /proc (Linux)")
def test_a_real_process_started_before_the_attempt_is_not_its_own(monkeypatch):
    """Real processes and this kernel's boot clock; only the environment read is faked hidden
    (making one hide it needs another account)."""
    old = _sleeper()
    try:
        time.sleep(START_MARGIN + 1.5)
        mark = proc_start.boot_mark()
        new = _sleeper()
        try:
            assert mark and proc_start.not_older([old.pid, new.pid], mark, START_MARGIN) == [
                new.pid
            ]
            _table(monkeypatch, [(old.pid, old.pid, ME, None)])
            assert sweep_marked("CAIRNWORK_ATTEMPT", "a1", dispatched=mark).gone
            assert not sweep_marked("CAIRNWORK_ATTEMPT", "a1").gone
        finally:
            new.kill()
            new.wait()
    finally:
        old.kill()
        old.wait()


@pytest.mark.skipif(proc_start.LINUX, reason="hosts without the boot clock (macOS)")
def test_a_real_old_process_still_holds_the_fence_off_linux(monkeypatch):
    old = _sleeper()
    try:
        _table(monkeypatch, [(old.pid, old.pid, ME, None)])
        assert proc_start.boot_mark() == ""
        assert not sweep_marked("CAIRNWORK_ATTEMPT", "a1", dispatched=MARK).gone
    finally:
        old.kill()
        old.wait()


def test_recovery_and_the_takeover_pass_the_dispatch_mark(tmp_path, monkeypatch):
    monkeypatch.setattr(proc_start, "boot_mark", lambda: MARK)
    ledger = OwnershipLedger(tmp_path / "own.sqlite")
    task_id = ledger.bind("k1", {"a": 1}, OWNER, "card-k1").record.task_id
    ledger.set_status(task_id, "DISPATCHED")
    journal = Journal(tmp_path / "journal.sqlite")
    journal.record(task_id, "a1", "DISPATCHED", str(tmp_path / "runs"))
    seen = []

    def sweep(var, value, **kw):
        seen.append(kw.get("dispatched"))
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
    assert set(seen) == {MARK}
    seen.clear()
    with pytest.raises(Exception, match="UNREADABLE_PROCESSES"):
        takeover.supersede(ledger, task_id)  # no journal: no mark, the strict rule
    assert set(seen) == {""}
