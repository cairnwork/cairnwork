"""#244: the sweep below its interface. ``sweep_marked`` says what it saw (``confined.Sweep``),
and only ``gone`` means none of the attempt's processes are left: a refused kill, a process
whose environment is hidden, a busy worker account, a table that cannot be read, or a killed
process still listed after the confirm window all keep the takeover fence. The process table
and the kill are faked; no other account and no sudo are used."""

import os
import signal
import subprocess
from types import SimpleNamespace

import pytest
from test_recovery import crash, settle
from test_recovery import project as project  # noqa: F401 - fixture

import cairnwork.confined as confined
from cairnwork import takeover
from cairnwork.confined import Sweep, confirm_gone, sweep_marked
from cairnwork.ownership import OWNER, OwnershipLedger, TakeoverRefused

ME = os.getuid()
FAKE = 4_000_001  # pids of the fake table; never signalled for real
OTHER_UID = ME + 4242


class _Everything:
    """An environment that holds every marker (the attempt id is not known in advance)."""

    def __contains__(self, _item):
        return True


def _table(monkeypatch, *looks, kill=None):
    """Each call to the process table returns the next look (the last repeats); os.kill of a
    fake pid goes to ``kill`` (default: delivered). Returns the signalled pids."""
    calls, killed = [], []
    real_kill = os.kill

    def processes():
        calls.append(1)
        return [tuple(row) for row in looks[min(len(calls), len(looks)) - 1]]

    def fake_kill(pid, sig):
        if pid < FAKE:
            return real_kill(pid, sig)
        killed.append(pid)
        if kill is not None:
            kill(pid)

    monkeypatch.setattr(confined, "_processes_with_environment", processes)
    monkeypatch.setattr(confined, "_reread", lambda pids: dict.fromkeys(pids))  # stays hidden
    monkeypatch.setattr(confined.os, "kill", fake_kill)
    monkeypatch.setattr(confined, "CONFIRM_WAIT", 0)
    monkeypatch.setattr(confined, "HIDDEN_WAIT", 0)
    return killed


def _marked(pid=FAKE, uid=ME):
    return (pid, pid, uid, {"CAIRNWORK_ATTEMPT=a1"})


def _denied(_pid):
    raise PermissionError("not permitted")


def test_a_refused_kill_is_a_survivor(monkeypatch):
    _table(monkeypatch, [_marked()], kill=_denied)
    found = sweep_marked("CAIRNWORK_ATTEMPT", "a1")
    assert (found.listed, found.killed, found.denied, found.gone) == (True, 0, 1, False)
    assert found.problem().startswith("SURVIVORS_REMAIN")
    # the run's escape check counts it too (NIT #244: it used to read as gone)
    _table(monkeypatch, [(FAKE, FAKE, ME, {f"{confined.RUN_TOKEN_VAR}=t"})], kill=_denied)
    assert confined._sweep_survivors("t", -1) == 1


def test_a_vanished_process_is_not_counted(monkeypatch):
    def vanished(_pid):
        raise ProcessLookupError

    _table(monkeypatch, [_marked()], kill=vanished)
    assert sweep_marked("CAIRNWORK_ATTEMPT", "a1").gone


def test_hidden_environments_and_other_users(monkeypatch):
    rows = [
        (FAKE, FAKE, ME, None),  # one of ours that hides its environment
        (FAKE + 1, FAKE + 1, OTHER_UID, None),  # another user's: not ours to read or signal
        (FAKE + 2, FAKE + 2, ME, {"CAIRNWORK_ATTEMPT=a10"}),  # a longer marker: not ours
        (FAKE + 3, 77, ME, {"CAIRNWORK_ATTEMPT=a1"}),  # in skip_group: just killed by the caller
    ]
    killed = _table(monkeypatch, rows)
    found = sweep_marked("CAIRNWORK_ATTEMPT", "a1", skip_group=77)
    assert (found.found, found.unreadable, found.account, found.gone) == (0, 1, 0, False)
    assert found.problem().startswith("UNREADABLE_PROCESSES") and not killed


def test_a_briefly_hidden_process_is_read_again(monkeypatch):
    """A process that is starting hides its environment for a moment (a busy host nearly
    always has one): it is read again, then judged like any other."""
    killed = _table(monkeypatch, [(FAKE, FAKE, ME, None), (FAKE + 1, FAKE + 1, ME, None)])
    answers = iter([{FAKE: {"CAIRNWORK_ATTEMPT=a1"}}])  # FAKE + 1 exited; FAKE is marked
    monkeypatch.setattr(confined, "_reread", lambda pids: next(answers))
    found = sweep_marked("CAIRNWORK_ATTEMPT", "a1")
    assert (found.killed, found.unreadable) == (1, 0) and killed == [FAKE]


def test_an_unknown_re_read_keeps_the_process_hidden(monkeypatch):
    _table(monkeypatch, [(FAKE, FAKE, ME, None)])
    calls = []
    monkeypatch.setattr(confined, "_reread", lambda pids: calls.append(pids))
    assert sweep_marked("CAIRNWORK_ATTEMPT", "a1").unreadable == 1
    assert len(calls) == confined.HIDDEN_TRIES


def test_the_worker_account_is_idle_or_the_sweep_is_not_gone(monkeypatch):
    rows = [(FAKE, FAKE, OTHER_UID, None)]
    _table(monkeypatch, rows)
    monkeypatch.setattr(confined, "_account_uid", lambda name: OTHER_UID if name == "w" else None)
    assert sweep_marked("CAIRNWORK_ATTEMPT", "a1").gone  # no account: another user's
    busy = sweep_marked("CAIRNWORK_ATTEMPT", "a1", account="w")
    assert busy.account == 1 and busy.problem().startswith("WORKER_ACCOUNT_BUSY")
    unknown = sweep_marked("CAIRNWORK_ATTEMPT", "a1", account="nobody-here")
    assert not unknown.listed and unknown.problem().startswith("SWEEP_UNAVAILABLE")
    _table(monkeypatch, [])
    assert sweep_marked("CAIRNWORK_ATTEMPT", "a1", account="w").gone


def test_a_killed_process_must_be_seen_gone_within_the_window(monkeypatch):
    """SIGKILL delivered is not a death: a process in uninterruptible I/O stays listed."""
    lingering = [[_marked()]] * confined.CONFIRM_TRIES + [[]]
    killed = _table(monkeypatch, *lingering)

    def look():
        return sweep_marked("CAIRNWORK_ATTEMPT", "a1")

    assert confirm_gone(look(), look).gone  # gone at the last look of the window
    assert len(killed) == confined.CONFIRM_TRIES
    _table(monkeypatch, *([[_marked()]] * (confined.CONFIRM_TRIES + 1)), [])
    last = confirm_gone(look(), look)
    assert not last.gone and last.killed == 1  # one look too many: still listed


def test_confirm_stops_at_an_unreadable_table():
    looks = []

    def again():
        looks.append(1)
        return Sweep(listed=False)

    assert not confirm_gone(Sweep(True, killed=1), again).listed and len(looks) == 1


def _failed(tmp_path, attempt="a1", account=""):
    ledger = OwnershipLedger(tmp_path / "own.sqlite")
    task_id = ledger.bind("k1", {"a": 1}, OWNER, "card-k1").record.task_id
    ledger.set_status(task_id, "DISPATCHED")
    ledger.set_status(task_id, "FAILED", fence=(attempt, account))
    return ledger, task_id


@pytest.mark.parametrize(
    ("rows", "kill", "code"),
    [
        ([_marked()], _denied, "SURVIVORS_REMAIN"),  # denied kills (Codex #244 MUST-FIX 1)
        ([(FAKE, FAKE, ME, None)], None, "UNREADABLE_PROCESSES"),  # partial enumeration
        ([_marked()], None, "SURVIVORS_REMAIN"),  # delayed termination past the window
    ],
)
def test_the_takeover_keeps_the_fence_below_the_interface(tmp_path, monkeypatch, rows, kill, code):
    ledger, task_id = _failed(tmp_path)
    _table(monkeypatch, rows, kill=kill)
    with pytest.raises(TakeoverRefused, match=code):
        takeover.supersede(ledger, task_id)
    assert ledger.get(task_id=task_id).status == "FAILED"
    assert ledger.unswept_attempt(task_id) is not None


def test_the_takeover_goes_ahead_once_a_slow_death_is_seen(tmp_path, monkeypatch):
    ledger, task_id = _failed(tmp_path)
    _table(monkeypatch, [_marked()], [_marked()], [])
    assert takeover.supersede(ledger, task_id).status == "SUPERSEDED"
    assert ledger.unswept_attempt(task_id) is None


def test_a_worker_account_attempt_needs_the_account_idle(tmp_path, monkeypatch):
    """#244 review: the owner cannot read the worker account's environments, so an attempt
    that ran as that account is fenced while the account runs anything (fakes, no sudo)."""
    ledger, task_id = _failed(tmp_path, account="w")
    monkeypatch.setattr(confined, "_account_uid", lambda name: OTHER_UID if name == "w" else None)
    _table(monkeypatch, [(FAKE, FAKE, OTHER_UID, None)])
    with pytest.raises(TakeoverRefused, match="WORKER_ACCOUNT_BUSY"):
        takeover.supersede(ledger, task_id)
    monkeypatch.setattr(confined, "_account_uid", lambda _name: None)  # cannot enumerate it
    with pytest.raises(TakeoverRefused, match="SWEEP_UNAVAILABLE"):
        takeover.supersede(ledger, task_id)
    monkeypatch.setattr(confined, "_account_uid", lambda name: OTHER_UID if name == "w" else None)
    _table(monkeypatch, [])
    assert takeover.supersede(ledger, task_id).status == "SUPERSEDED"


@pytest.mark.parametrize(("looks", "fenced"), [(2, False), (99, True)])
def test_recovery_fences_a_kill_not_seen_through(project, tmp_path, monkeypatch, looks, fenced):  # noqa: F811
    """Codex #244 MUST-FIX 3: recovery's first sweep killing something is not enough."""
    crash(project, tmp_path, "CANDIDATE")
    row = (FAKE, FAKE, ME, _Everything())
    _table(monkeypatch, *([[row]] * looks), [])
    (decision,), ledger = settle(tmp_path)
    assert decision.new_status == "FAILED" and decision.killed_processes == 1
    assert decision.sweep_unavailable is fenced
    held = ledger.unswept_attempt(decision.task_id)
    assert (held is not None) is fenced
    if fenced:
        assert held is not None and held.attempt_id == decision.attempt_id
        assert decision.detail.startswith("SURVIVORS_REMAIN")


def _ps(monkeypatch, *answers):
    calls = []

    def run(argv, **_kw):
        calls.append(argv)
        code, out, err = answers[min(len(calls), len(answers)) - 1]
        return SimpleNamespace(returncode=code, stdout=out, stderr=err)

    monkeypatch.setattr(confined.subprocess, "run", run)
    return calls


@pytest.mark.skipif(os.uname().sysname != "Darwin", reason="ps output (macOS)")
def test_ps_rows_carry_the_uid_and_a_hidden_environment(monkeypatch):
    out = (
        f"  10   10 {ME} Ss   /bin/sleep 9 CAIRNWORK_ATTEMPT=a1\n"
        f"  11   11 {ME} R    (git)\n"
        f"  12   12 {ME} Z    (zombie)\n"
        f"  13   13 0 S    /sbin/launchd\n"
        "garbage line\n"
    )
    _ps(monkeypatch, (0, out, ""))
    rows = confined._processes_with_environment()
    assert rows is not None
    assert [(r[0], r[2]) for r in rows] == [(10, ME), (11, ME), (13, 0)]
    assert "CAIRNWORK_ATTEMPT=a1" in rows[0][3] and rows[1][3] is None


@pytest.mark.skipif(os.uname().sysname != "Darwin", reason="ps -p (macOS)")
def test_re_reading_hidden_pids(monkeypatch):
    calls = _ps(monkeypatch, (0, f" 11 11 {ME} S /bin/sh CAIRNWORK_ATTEMPT=a1\n", ""))
    assert confined._reread({11, 12}) == {11: {"/bin/sh", "CAIRNWORK_ATTEMPT=a1"}}
    assert calls[0][-2:] == ["-p", "11,12"]
    _ps(monkeypatch, (1, "", ""))
    assert confined._reread({11}) == {}  # none running
    _ps(monkeypatch, (1, "", "ps: illegal option"))
    assert confined._reread({11}) is None  # an error is not "gone"

    def timeout(argv, **kw):
        raise subprocess.TimeoutExpired(argv, 1)

    monkeypatch.setattr(confined.subprocess, "run", timeout)
    assert confined._reread({11}) is None


def _proc(root, pid, uid, *, environ=b"A=1\0", pgid=None, stat_comm="sleep", real=None):
    base = root / str(pid)
    base.mkdir()
    ids = f"{real if real is not None else uid}\t{uid}\t{uid}\t{uid}"  # real, effective, ...
    (base / "status").write_text(f"Name:\t{stat_comm}\nUid:\t{ids}\n")
    (base / "stat").write_text(f"{pid} ({stat_comm}) S 1 {pgid or pid} {pgid or pid} 0\n")
    if environ is not None:
        (base / "environ").write_bytes(environ)
    return base


@pytest.mark.skipif(ME == 0, reason="root reads every file")
def test_linux_rows_from_proc(tmp_path):
    _proc(tmp_path, 10, ME, environ=b"CAIRNWORK_ATTEMPT=a1\0HOME=/h\0", stat_comm="a) b")
    hidden = _proc(tmp_path, 11, ME)  # one of ours, not dumpable: environ unreadable
    (hidden / "environ").chmod(0)
    other = _proc(tmp_path, 12, OTHER_UID)  # another user's: environ unreadable to us
    (other / "environ").chmod(0)
    (tmp_path / "13").mkdir()  # exited between the listing and the reads
    _proc(tmp_path, 14, ME, real=OTHER_UID)  # the effective uid decides: ours
    (tmp_path / "self").mkdir()
    try:
        rows = sorted(confined._linux_processes(str(tmp_path)) or [], key=lambda r: r[0])
    finally:
        (hidden / "environ").chmod(0o600)
        (other / "environ").chmod(0o600)
    assert [(r[0], r[1], r[2]) for r in rows] == [
        (10, 10, ME), (11, 11, ME), (12, 12, OTHER_UID), (14, 14, ME)
    ]  # fmt: skip
    assert "CAIRNWORK_ATTEMPT=a1" in rows[0][3] and rows[1][3] is None and rows[2][3] is None
    assert confined._linux_processes(str(tmp_path / "missing")) is None


@pytest.mark.skipif(ME == 0, reason="root reads every file")
def test_a_process_whose_owner_cannot_be_read_is_never_counted_gone(tmp_path, monkeypatch):
    """#244 review (Linux, hidepid=1): an unreadable status gave pgid -1, the default
    skip_group, so the process was dropped and the sweep read as gone."""
    marked = _proc(tmp_path, FAKE, ME, environ=b"CAIRNWORK_ATTEMPT=a1\0")
    worker = _proc(tmp_path, FAKE + 1, OTHER_UID)  # the worker account's: nothing readable
    unreadable = [marked / "status", worker / "status", worker / "environ"]
    for path in unreadable:
        path.chmod(0)
    try:
        rows = sorted(confined._linux_processes(str(tmp_path)) or [], key=lambda r: r[0])
    finally:
        for path in unreadable:
            path.chmod(0o600)
    assert [(r[0], r[1], r[2]) for r in rows] == [(FAKE, None, ME), (FAKE + 1, None, ME)]
    killed = _table(monkeypatch, rows)
    monkeypatch.setattr(confined, "_account_uid", lambda name: OTHER_UID)
    found = sweep_marked("CAIRNWORK_ATTEMPT", "a1", account="w")
    assert (found.killed, found.unreadable, found.gone) == (1, 1, False) and killed == [FAKE]
    assert found.pids == (FAKE, FAKE + 1)
    assert sweep_marked("CAIRNWORK_ATTEMPT", "a1", skip_group=-1).killed == 1  # not dropped


def test_marked_processes_are_killed_before_hidden_ones_are_read_again(monkeypatch):
    """#244 review NIT: the re-read of a hidden process waits, so the pids the listing shows
    marked are killed first; a listed pid could otherwise be reused meanwhile."""
    events = []
    _table(monkeypatch, [_marked(), (FAKE + 1, FAKE + 1, ME, None)], kill=events.append)

    def reread(pids):
        events.append(("reread", sorted(pids)))
        return {FAKE + 1: {"CAIRNWORK_ATTEMPT=a1"}}

    monkeypatch.setattr(confined, "_reread", reread)
    found = sweep_marked("CAIRNWORK_ATTEMPT", "a1")
    assert events == [FAKE, ("reread", [FAKE + 1]), FAKE + 1]
    assert (found.killed, found.unreadable, found.pids) == (2, 0, (FAKE, FAKE + 1))


def test_the_pids_that_keep_the_fence_are_named(monkeypatch):
    rows = [(FAKE, FAKE, OTHER_UID, None), (FAKE + 1, FAKE + 1, ME, None), _marked(FAKE + 2)]
    _table(monkeypatch, rows + [(FAKE + 3, FAKE + 3, ME, {"A=1"})], kill=_denied)
    monkeypatch.setattr(confined, "_account_uid", lambda name: OTHER_UID)
    found = sweep_marked("CAIRNWORK_ATTEMPT", "a1", account="w")
    assert found.pids == (FAKE, FAKE + 1, FAKE + 2)  # account, hidden, denied; not FAKE + 3
    monkeypatch.setattr(confined, "_names", lambda pids: {FAKE: "ssh-agent"})
    assert confined.describe(found.pids, limit=2) == f"{FAKE} ssh-agent, {FAKE + 1} ? and 1 more"
    assert confined.describe(()) == ""


def test_describe_reads_real_command_names():
    assert "python" in confined.describe((os.getpid(),)).lower()


def test_a_real_marked_process_is_killed_and_seen_gone():
    import secrets
    import sys

    value = "a" + secrets.token_hex(8)
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        env={**os.environ, "CW_SWEEP_TEST": value},
    )
    try:
        deadline_looks = 0
        found = sweep_marked("CW_SWEEP_TEST", value)
        while found.killed == 0 and deadline_looks < 50:  # the child may not be listed yet
            deadline_looks += 1
            found = sweep_marked("CW_SWEEP_TEST", value)
        assert found.killed == 1
        child.wait(timeout=10)  # reaped here, as its parent
        last = confirm_gone(found, lambda: sweep_marked("CW_SWEEP_TEST", value))
        assert last.found == 0 and last.listed
    finally:
        if child.poll() is None:
            child.send_signal(signal.SIGKILL)
            child.wait()
