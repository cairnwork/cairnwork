"""#301: macOS 27 withholds the environment of Apple platform binaries (/bin/sh, /bin/sleep)
from ps -E, so the CAIRNWORK_ATTEMPT marker cannot be seen on them. Recovery ties such an owner
process to the dead attempt by its working directory (``sweep_marked(cwd_dirs=...)``, proc_cwd).
The sweep-level tests fake the process table and proc_cwd's readers; the last two start a real
marked /bin/sh and /bin/sleep and are skipped where /bin/sleep's environment is not withheld.
No test reads, prints or asserts on the environment or arguments of a real process."""

import os
import secrets
import signal
import subprocess
import time
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from test_sweep_result import FAKE, ME, OTHER_UID, _marked, _table

import cairnwork.confined as confined
import cairnwork.recovery as recovery
from cairnwork import proc_cwd
from cairnwork.confined import ATTEMPT_ENV, Sweep, sweep_marked
from cairnwork.journal import Journal
from cairnwork.notary import Notary
from cairnwork.ownership import OWNER, OwnershipLedger

ATTEMPT = "a0123456789abcdef"


def _plain(pid=FAKE, uid=ME):
    """A readable row with no marker: what ps -E shows for /bin/sh or /bin/sleep."""
    return (pid, pid, uid, {"/bin/sleep", "60"})


@pytest.fixture
def readers(monkeypatch):
    """proc_cwd's readers faked: a pid in ``cwds`` hides its environment and sits there; a pid
    in ``unknown`` cannot be read; any other shows its environment. Returns (cwds, unknown,
    calls)."""
    cwds, unknown, calls = {}, set(), []
    monkeypatch.setattr(proc_cwd, "_MACOS", True)

    def withheld(pid):
        calls.append(("withheld", pid))
        return None if pid in unknown else pid in cwds

    def cwd_of(pid):
        calls.append(("cwd", pid))
        return cwds.get(pid)

    monkeypatch.setattr(proc_cwd, "environment_withheld", withheld)
    monkeypatch.setattr(proc_cwd, "cwd_of", cwd_of)
    monkeypatch.setattr(proc_cwd, "_started", lambda pid: b"same start")  # pid not reused
    return cwds, unknown, calls


@pytest.fixture
def runs(tmp_path):
    root = tmp_path / "runs"
    for name in (ATTEMPT, f"{ATTEMPT}-q-v0.work", f"{ATTEMPT}0-q-v0.work"):
        (root / name / "sub").mkdir(parents=True)
    return root


def _dirs(runs):
    return (str((runs / ATTEMPT).resolve()), str((runs / f"{ATTEMPT}-q-v0.work").resolve()))


def test_a_withheld_process_in_an_attempt_dir_is_killed_and_labelled_cwd(
    monkeypatch, readers, runs
):
    cwds, _, _ = readers
    cwds[FAKE] = str(runs / f"{ATTEMPT}-q-v0.work" / "sub")  # unresolved: tmp is a symlink
    cwds[FAKE + 1] = str(runs / ATTEMPT)
    killed = _table(monkeypatch, [_plain(FAKE), _plain(FAKE + 1)])
    found = sweep_marked(ATTEMPT_ENV, ATTEMPT, cwd_dirs=_dirs(runs))
    assert killed == [FAKE, FAKE + 1]
    assert (found.killed, found.denied, found.found, found.gone) == (2, 0, 2, False)
    assert found.held_by == ((FAKE, "cwd"), (FAKE + 1, "cwd"))
    assert found.problem().startswith("SURVIVORS_REMAIN")
    assert "cwd" in confined.KEPT_BY


def test_a_withheld_process_outside_the_attempt_dirs_is_not_killed(monkeypatch, readers, runs):
    cwds, _, _ = readers
    cwds[FAKE] = str(runs / f"{ATTEMPT}0-q-v0.work")  # another attempt: shares a name prefix
    cwds[FAKE + 1] = str(runs)  # the run root itself
    cwds[FAKE + 2] = "/"
    cwds[FAKE + 3] = ""
    rows = [_plain(FAKE + i) for i in range(4)]
    killed = _table(monkeypatch, rows)
    found = sweep_marked(ATTEMPT_ENV, ATTEMPT, cwd_dirs=_dirs(runs))
    assert killed == [] and found.gone and found.held_by == ()


def test_an_unknown_answer_is_ignored(monkeypatch, readers, runs):
    cwds, unknown, calls = readers
    unknown.add(FAKE)  # environment_withheld: None
    cwds[FAKE] = str(runs / ATTEMPT)
    cwds[FAKE + 1] = None  # withheld, but its cwd cannot be read
    killed = _table(monkeypatch, [_plain(FAKE), _plain(FAKE + 1), _plain(FAKE + 2)])
    found = sweep_marked(ATTEMPT_ENV, ATTEMPT, cwd_dirs=_dirs(runs))
    assert killed == [] and found.gone
    assert ("cwd", FAKE) not in calls and ("cwd", FAKE + 2) not in calls  # not withheld: no read


def test_a_refused_kill_is_a_denied_cwd_survivor(monkeypatch, readers, runs):
    cwds, _, _ = readers
    cwds[FAKE] = str(runs / ATTEMPT)

    def refuse(_pid):
        raise PermissionError

    _table(monkeypatch, [_plain()], kill=refuse)
    found = sweep_marked(ATTEMPT_ENV, ATTEMPT, cwd_dirs=_dirs(runs))
    assert (found.killed, found.denied, found.held_by) == (0, 1, ((FAKE, "cwd"),))
    assert not found.gone


def test_a_withheld_process_that_exits_before_its_kill_is_vanished(monkeypatch, readers, runs):
    cwds, _, _ = readers
    cwds[FAKE] = str(runs / ATTEMPT)

    def gone(_pid):
        raise ProcessLookupError

    _table(monkeypatch, [_plain()], kill=gone)
    found = sweep_marked(ATTEMPT_ENV, ATTEMPT, cwd_dirs=_dirs(runs))
    assert (found.vanished, found.found, found.gone) == (1, 0, False)


def test_only_the_owners_unmarked_readable_rows_are_read(monkeypatch, readers, runs):
    cwds, _, calls = readers
    for pid in range(FAKE, FAKE + 4):
        cwds[pid] = str(runs / ATTEMPT)
    marked = (FAKE, FAKE, ME, {f"{ATTEMPT_ENV}={ATTEMPT}"})
    hidden = (FAKE + 1, FAKE + 1, ME, None)  # ps showed "(name)": today's hidden path
    killed = _table(monkeypatch, [marked, hidden, _plain(FAKE + 2, OTHER_UID), _plain(FAKE + 3)])
    found = sweep_marked(ATTEMPT_ENV, ATTEMPT, cwd_dirs=_dirs(runs))
    assert {pid for _, pid in calls} == {FAKE + 3}
    assert sorted(killed) == [FAKE, FAKE + 3]
    assert found.held_by == ((FAKE, "marked"), (FAKE + 1, "hidden"), (FAKE + 3, "cwd"))


def test_without_cwd_dirs_nothing_more_is_read(monkeypatch, runs):
    monkeypatch.setattr(proc_cwd, "_MACOS", True)

    def fail(_pid):
        raise AssertionError("read without cwd_dirs")

    monkeypatch.setattr(proc_cwd, "environment_withheld", fail)
    monkeypatch.setattr(proc_cwd, "cwd_of", fail)
    killed = _table(monkeypatch, [_plain(), _marked(FAKE + 1)])
    found = sweep_marked("CAIRNWORK_ATTEMPT", "a1")
    assert killed == [FAKE + 1] and found.held_by == ((FAKE + 1, "marked"),)


def _dispatched(state, run_root, attempt, dispatched_root=None):
    ledger = OwnershipLedger(state / "own.sqlite")
    task = ledger.bind("k301", {"a": 1}, OWNER, "card-k301").record.task_id
    ledger.set_status(task, "DISPATCHED")
    journal = Journal(state / "journal.sqlite")
    journal.record(task, attempt, "DISPATCHED", str(dispatched_root or run_root))
    return ledger, journal


def _recover(state, run_root, ledger, journal):
    notary = Notary(state / "notary.sqlite")
    return recovery.recover(ledger=ledger, notary=notary, journal=journal, run_root=run_root)


def test_recovery_passes_the_attempts_run_dirs_before_removing_them(tmp_path, monkeypatch):
    state, run_root, other = tmp_path / "state", tmp_path / "runs", tmp_path / "dispatched"
    state.mkdir()
    for root in (run_root, other):
        (root / f"{ATTEMPT}-q-v0.work").mkdir(parents=True)
    (run_root / "unrelated").mkdir()
    seen = []

    def sweep(var, value, **kw):
        seen.append((value, kw.get("cwd_dirs"), (run_root / f"{ATTEMPT}-q-v0.work").exists()))
        return Sweep(listed=True)

    monkeypatch.setattr(recovery, "sweep_marked", sweep)
    monkeypatch.setattr(confined, "CONFIRM_WAIT", 0)
    ledger, journal = _dispatched(state, run_root, ATTEMPT, other)
    (decision,) = _recover(state, run_root, ledger, journal)
    expected = tuple(str((r / f"{ATTEMPT}-q-v0.work").resolve()) for r in (run_root, other))
    assert seen and set(seen) == {(ATTEMPT, expected, True)}
    assert decision.detail == "" and not (run_root / f"{ATTEMPT}-q-v0.work").exists()


def test_the_verifier_accounts_run_root_is_a_cwd_root(tmp_path):
    owner_root, account_root = tmp_path / "runs", tmp_path / "account"
    (owner_root / f"{ATTEMPT}-v-v0").mkdir(parents=True)
    (account_root / f"{ATTEMPT}-v-v0.work").mkdir(parents=True)
    account = SimpleNamespace(run_root=account_root)
    assert recovery._cwd_dirs([owner_root], account, ATTEMPT) == (
        str((owner_root / f"{ATTEMPT}-v-v0").resolve()),
        str((account_root / f"{ATTEMPT}-v-v0.work").resolve()),
    )
    assert recovery._cwd_dirs([owner_root], None, ATTEMPT) == (
        str((owner_root / f"{ATTEMPT}-v-v0").resolve()),
    )


# ---- real processes: macOS 27 withholds /bin/sleep's environment --------------------------


@pytest.fixture
def withheld_here():
    probe = subprocess.Popen(  # noqa: S603 - fixed argv
        ["/bin/sleep", "30"], stdin=subprocess.DEVNULL, env={"PATH": "/usr/bin:/bin"}
    )
    try:
        answer = proc_cwd.environment_withheld(probe.pid)
    finally:
        probe.kill()
        probe.wait()
    if answer is not True:
        pytest.skip(f"/bin/sleep's environment is not withheld here ({answer!r}): nothing to tie")


@contextmanager
def _left_sleeper(cwd, attempt, pidfile):
    """A dead run's leftover: a marked /bin/sh that started a marked /bin/sleep in the
    background and exited. Yields (process group, sleep pid); the group (the shell's pid, from
    Popen) is SIGKILLed at the end whatever happens."""
    shell = subprocess.Popen(  # noqa: S603 - fixed argv
        ["/bin/sh", "-c", '/bin/sleep 60 & echo $! > "$0"; exit 0', str(pidfile)],
        cwd=cwd,
        env={"PATH": "/usr/bin:/bin", ATTEMPT_ENV: attempt},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        shell.wait(timeout=10)
        yield shell.pid, int(pidfile.read_text())
    finally:
        try:
            os.killpg(shell.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


def _group_alive(pgid):
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    return True


def _gone_within(pgid, seconds=5.0):
    deadline = time.monotonic() + seconds
    while _group_alive(pgid):
        if time.monotonic() > deadline:
            return False
        time.sleep(0.05)
    return True


def test_recovery_kills_a_withheld_leftover_in_the_attempts_run_dir(tmp_path, withheld_here):
    run_root, state = tmp_path.resolve(), tmp_path / "state"
    state.mkdir()
    attempt = "a" + secrets.token_hex(8)
    work = run_root / f"{attempt}-q-v0.work"
    work.mkdir()
    ledger, journal = _dispatched(state, run_root, attempt)
    with _left_sleeper(work, attempt, tmp_path / "sleep.pid") as (group, sleeper):
        assert proc_cwd.environment_withheld(sleeper) is True  # the marker cannot be seen
        (decision,) = _recover(state, run_root, ledger, journal)
        assert _gone_within(group), "the withheld leftover survived recovery"
    assert (decision.new_status, decision.detail) == ("FAILED", "")
    assert decision.killed_processes == 1 and not decision.sweep_unavailable


def test_recovery_leaves_a_withheld_process_outside_the_attempts_dirs(tmp_path, withheld_here):
    run_root, state = tmp_path.resolve(), tmp_path / "state"
    state.mkdir()
    attempt = "a" + secrets.token_hex(8)
    (run_root / attempt).mkdir()
    (run_root / f"{attempt}-q-v0.work").mkdir()
    other = run_root / f"{attempt}0-q-v0.work"  # another attempt's dir: shares a name prefix
    other.mkdir()
    ledger, journal = _dispatched(state, run_root, attempt)
    with (
        _left_sleeper(other, attempt, tmp_path / "other.pid") as (beside, _),
        _left_sleeper(run_root, attempt, tmp_path / "root.pid") as (in_root, _),
    ):
        (decision,) = _recover(state, run_root, ledger, journal)
        assert _group_alive(beside) and _group_alive(in_root)
    assert decision.killed_processes == 0
