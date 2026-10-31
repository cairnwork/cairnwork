"""#63 (a): agy on the owner's uid (no agy_run_as_user) must not end FAILED unfenced while a
process of its attempt may still run.

agy's own sweep (``_stop``) kills marked processes that escaped its group. Its answer used to be
ignored: a survivor the kill could not reach, or a process table that could not be read, ended
the attempt as an ordinary AgyWorkerError, so the task went FAILED without an unswept mark and a
new attempt could SUPERSEDE it while the old one still ran. The worker-account path already
raises WorkerMayLive (WORKER_SWEEP_UNCLEAN, #244); the owner path must do the same with the
owner's account (""), so pipeline.py fences the attempt and only takeover.py, after its own
sweep, releases the card. Fake agy; no model is called."""

import io
import os
import signal
import subprocess
import time

import pytest
from agy_receipt_lines import WORKER, good_run, status
from test_agy_worker import TASK, attempt, expire_once_ready, fake_agy
from test_agy_worker_account import ROOT, _replaying
from test_pipeline import project as project  # noqa: F401 - fixture
from test_pipeline import task
from test_recovery import stores

import cairnwork.confined as confined
from cairnwork import takeover
from cairnwork.confined import UNLISTED, Sweep
from cairnwork.ownership import TakeoverRefused
from cairnwork.pipeline import run_task
from cairnwork.unswept import Mark, WorkerMayLive
from cairnwork.workers import agy as agy_module
from cairnwork.workers.agy import AgyWorker, AgyWorkerError
from cairnwork.workers.agy_dispatch import AgyWorkerAccountFailed, Dispatch

UNCLEAN = [UNLISTED, Sweep(True, denied=1), Sweep(True, unreadable=1)]


def _answers(monkeypatch, answer):
    """agy's sweep says `answer` on every look."""
    monkeypatch.setattr(agy_module, "sweep_marked", lambda *_a, **_k: answer)
    monkeypatch.setattr(confined, "CONFIRM_WAIT", 0)


def _workdir(tmp_path, n):
    workdir = tmp_path / attempt(n)
    workdir.mkdir()
    return workdir


@pytest.mark.parametrize("answer", UNCLEAN)
def test_an_owner_mode_agy_whose_sweep_is_unclean_may_live(tmp_path, monkeypatch, answer):
    _answers(monkeypatch, answer)
    agy = fake_agy(tmp_path, "print('done', flush=True)")
    with pytest.raises(WorkerMayLive, match="WORKER_SWEEP_UNCLEAN") as caught:
        AgyWorker(agy=agy).run(_workdir(tmp_path, 1), TASK)
    assert caught.value.account == ""  # the owner's uid: takeover sweeps the owner's processes


@pytest.mark.parametrize("answer", UNCLEAN)
def test_a_timed_out_owner_mode_agy_with_an_unclean_sweep_may_live(tmp_path, monkeypatch, answer):
    """The window the audit named: the timeout kill, then a sweep that cannot show them gone."""
    _answers(monkeypatch, answer)
    ready = tmp_path / "ready"
    agy = fake_agy(tmp_path, f"import time\nopen({str(ready)!r}, 'w').write('x')\ntime.sleep(60)")
    expire_once_ready(monkeypatch, ready)
    with pytest.raises(WorkerMayLive, match="WORKER_SWEEP_UNCLEAN"):
        AgyWorker(agy=agy, timeout_seconds=900).run(_workdir(tmp_path, 2), TASK)


def test_a_marked_survivor_the_kill_cannot_reach_fences_the_run(tmp_path, monkeypatch):
    """A real escaped process (setsid) that refuses SIGKILL (a kill the owner may not send) is
    still listed after the sweep: the attempt may live."""
    pid_file = tmp_path / "survivor.pid"
    agy = fake_agy(
        tmp_path,
        "import subprocess\n"
        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],"
        " start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
        f"open({str(pid_file)!r}, 'w').write(str(p.pid))",
    )
    real_kill = os.kill

    def kill(pid, sig):
        if pid_file.exists() and pid == int(pid_file.read_text()) and sig == signal.SIGKILL:
            raise PermissionError(1, "Operation not permitted")
        return real_kill(pid, sig)

    monkeypatch.setattr(confined.os, "kill", kill)
    monkeypatch.setattr(confined, "CONFIRM_WAIT", 0)
    try:
        with pytest.raises(WorkerMayLive, match="SURVIVORS_REMAIN"):
            AgyWorker(agy=agy).run(_workdir(tmp_path, 3), TASK)
    finally:
        monkeypatch.undo()
        if pid_file.exists():
            try:
                os.kill(int(pid_file.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_a_clean_sweep_after_a_kill_still_succeeds(tmp_path):
    """The fix must not fence an escaped process the sweep did kill (shown gone twice)."""
    agy = fake_agy(
        tmp_path,
        "import subprocess\n"
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],"
        " start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)",
    )
    started = time.monotonic()
    AgyWorker(agy=agy).run(_workdir(tmp_path, 4), TASK)
    assert time.monotonic() - started < 30


def test_every_look_skips_hidden_processes_older_than_agy(tmp_path, monkeypatch):
    """A process whose environment stays hidden and that predates the run (Linux: a same-uid
    ssh-agent, not dumpable) is not the run's: every look gets the boot mark taken before agy
    started, as takeover.py's does, so it cannot fence every run."""
    order, seen = [], []
    monkeypatch.setattr(agy_module.proc_start, "boot_mark", lambda: order.append("mark") or "b 5.0")
    popen = agy_module.subprocess.Popen

    def started(*args, **kwargs):
        order.append("start")
        return popen(*args, **kwargs)

    def look(*_args, **kwargs):
        seen.append(kwargs.get("dispatched"))
        return Sweep(True)

    monkeypatch.setattr(agy_module.subprocess, "Popen", started)
    monkeypatch.setattr(agy_module, "sweep_marked", look)
    monkeypatch.setattr(confined, "CONFIRM_WAIT", 0)
    AgyWorker(agy=fake_agy(tmp_path, "print('done', flush=True)")).run(_workdir(tmp_path, 5), TASK)
    assert order[:2] == ["mark", "start"] and seen == ["b 5.0", "b 5.0"]


@pytest.mark.parametrize("answer", UNCLEAN)
def test_a_worker_account_run_whose_own_sweep_is_unclean_is_fenced_on_that_account(
    tmp_path, monkeypatch, answer
):
    """With agy_run_as_user too, clean receipts and exit 0: the fence is on the worker account,
    whose takeover sweep also kills the owner's marked processes (confined.sweep_marked)."""
    _answers(monkeypatch, answer)
    worker, workdir, _ = _replaying(tmp_path, good_run(ROOT))
    with pytest.raises(WorkerMayLive, match="WORKER_SWEEP_UNCLEAN") as caught:
        worker.run(workdir, TASK)
    assert caught.value.account == WORKER


def _settling_raises(monkeypatch, exc):
    def settle(*_args, **_kwargs):
        raise exc

    monkeypatch.setattr(agy_module, "_settle_worker_run", settle)


def test_a_worker_account_failure_with_an_unclean_sweep_is_fenced_from_it(tmp_path, monkeypatch):
    _answers(monkeypatch, UNLISTED)
    _settling_raises(monkeypatch, AgyWorkerAccountFailed("WORKER_RECEIPT_MISSING"))
    worker, workdir, _ = _replaying(tmp_path, good_run(ROOT))
    with pytest.raises(WorkerMayLive, match="WORKER_SWEEP_UNCLEAN") as caught:
        worker.run(workdir, TASK)
    assert isinstance(caught.value.__cause__, AgyWorkerAccountFailed)


def test_an_interrupt_is_not_replaced_by_the_fence(tmp_path, monkeypatch):
    """An interrupt ends the run like a crash (the task stays DISPATCHED, recovery sweeps it); a
    WorkerMayLive in its place would let the pass go on to the next task."""
    _answers(monkeypatch, UNLISTED)
    _settling_raises(monkeypatch, KeyboardInterrupt())
    worker, workdir, _ = _replaying(tmp_path, good_run(ROOT))
    with pytest.raises(KeyboardInterrupt):
        worker.run(workdir, TASK)


class _Ended:
    """A dispatcher or agy that already ended with `code`; with `timeout` our wait expires
    first. No process is started: pid -1 is never signalled (os.kill and killpg are refused)."""

    pid = -1

    def __init__(self, code: int, timeout: bool):
        self.returncode, self.timeout = code, timeout
        self.stdout, self.stderr = io.BytesIO(), io.BytesIO()

    def wait(self, timeout=None):
        if self.timeout:
            self.timeout = False
            raise subprocess.TimeoutExpired("agy", timeout or 0)
        return self.returncode


class _Reader:
    """Complete receipts (the settle helpers are faked to say so) and a final status line, as
    the dispatcher writes last (without one the run is fenced, #546); status() reads none."""

    unknown = dropped = False

    def lines(self):
        return [status().rstrip("\n")]

    def status(self):
        return {}

    def describe(self):
        return "no status"


WORKERS = [None, Dispatch("dispatcher"), Dispatch("dispatcher", run_as_user=WORKER)]


def _ending(tmp_path, monkeypatch, dispatch, code, timeout):
    def refused(*_a, **_k):
        raise AssertionError("no signal is sent in this test")

    monkeypatch.setattr(os, "kill", refused)
    monkeypatch.setattr(os, "killpg", refused)
    monkeypatch.setattr(agy_module, "_stop", lambda *_a, **_k: (False, Sweep(listed=True)))
    monkeypatch.setattr(agy_module.agy_dispatch, "ask_to_stop", lambda _p: None)
    monkeypatch.setattr(agy_module.agy_receipts, "sweep_after_kill", lambda *_a, **_k: [])
    monkeypatch.setattr(agy_module.agy_receipts, "problems", lambda _r: [])
    monkeypatch.setattr(agy_module.agy_receipts, "completion_problem", lambda _r: None)
    monkeypatch.setattr(agy_module.agy_receipts, "write_record", lambda *_a, **_k: None)
    worker = AgyWorker(dispatch=dispatch)
    reader = None if dispatch is None else _Reader()
    attempt_ = ("attempt-1", "")
    return lambda: worker._await(
        _Ended(code, timeout), None, reader, attempt_, io.BytesIO(), tmp_path / "log"
    )


@pytest.mark.parametrize("timeout", [False, True], ids=["external", "our-timeout"])
@pytest.mark.parametrize("dispatch", WORKERS, ids=["raw-agy", "owner-dispatch", "worker-account"])
@pytest.mark.parametrize("code", [-9, -15])
def test_any_end_by_a_signal_is_fenced(tmp_path, monkeypatch, dispatch, timeout, code):
    """A signal end, ours (the timeout kill) or not, with complete receipts and a clean owner
    sweep: a launch after the last receipt may be unrecorded, so the run may live (#244).
    Fencing our own timeout kill costs one sweep before a takeover. Fakes only: no signal is
    sent, no process table is read, no wrapper sweep runs."""
    run = _ending(tmp_path, monkeypatch, dispatch, code, timeout)
    with pytest.raises(WorkerMayLive, match=f"^WORKER_SWEEP_UNCLEAN: .*KILLED_BY_SIGNAL: {-code}"):
        run()


@pytest.mark.parametrize("timeout", [False, True], ids=["exit", "our-timeout"])
@pytest.mark.parametrize("dispatch", WORKERS, ids=["raw-agy", "owner-dispatch", "worker-account"])
@pytest.mark.parametrize("code", [0, 1])
def test_an_exit_that_is_not_a_signal_is_not_fenced(tmp_path, monkeypatch, dispatch, timeout, code):
    run = _ending(tmp_path, monkeypatch, dispatch, code, timeout)
    if code == 0 and not timeout:
        run()  # unchanged: a clean exit with complete receipts succeeds
        return
    why = "agy timed out" if timeout else "agy exited 1"
    with pytest.raises(AgyWorkerError, match=f"^{why}") as caught:
        run()
    assert not isinstance(caught.value, WorkerMayLive)


@pytest.mark.sandbox
def test_an_owner_mode_agy_that_may_live_leaves_its_task_fenced(project, tmp_path, monkeypatch):  # noqa: F811
    """End to end: the FAILED task carries the mark, SUPERSEDED is refused, and the takeover
    releases the card only once its own sweep shows the attempt gone."""
    _answers(monkeypatch, UNLISTED)
    agy = fake_agy(tmp_path, "print('done', flush=True)")
    ledger, notary, journal = stores(tmp_path)
    result = run_task(
        task(project), ledger=ledger, notary=notary, journal=journal,
        worker=AgyWorker(agy=agy), run_root=tmp_path / "runs",
    )  # fmt: skip
    assert result.status == "FAILED" and "WORKER_SWEEP_UNCLEAN" in result.reason
    assert ledger.unswept_attempt(result.task_id) == Mark(result.attempt_id, "", "")
    with pytest.raises(TakeoverRefused, match="UNSWEPT_ATTEMPT"):
        ledger.set_status(result.task_id, "SUPERSEDED")
    monkeypatch.setattr(takeover, "sweep_marked", lambda *_a, **_k: UNLISTED)
    with pytest.raises(TakeoverRefused, match="SWEEP_UNAVAILABLE"):
        takeover.supersede(ledger, result.task_id, journal=journal)
    monkeypatch.setattr(takeover, "sweep_marked", lambda *_a, **_k: Sweep(listed=True))
    done = takeover.supersede(ledger, result.task_id, journal=journal)
    assert done.status == "SUPERSEDED" and ledger.unswept_attempt(result.task_id) is None
