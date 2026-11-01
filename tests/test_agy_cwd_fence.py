"""#537: agy's own sweep also ties a process to the attempt by a working directory inside the
clone (macOS withholds the environment of platform binaries such as /bin/sleep), and a run whose
sweep tied one so is fenced, even once later looks show it gone. Stand-ins only, built on the
fakes of test_agy_owner_sweep.py: no signal is sent and no process table is read, except the fake
agy binary, which is this test's own child."""

import io
import os
import threading

import pytest
from agy_receipt_lines import WORKER
from test_agy_owner_sweep import WORKERS, _Ended, _ending, _Reader
from test_agy_worker import TASK, attempt, fake_agy

from cairnwork.confined import Sweep
from cairnwork.unswept import WorkerMayLive
from cairnwork.workers import agy as agy_module
from cairnwork.workers.agy import AgyWorker


def test_agys_own_sweep_looks_for_withheld_processes_in_the_clone(tmp_path, monkeypatch):
    """#537: macOS withholds the environment of platform binaries (/bin/sleep), so one that agy
    left behind is tied to the attempt only by a working directory inside the clone; agy's sweep
    looks there, by the realpath taken before agy started, as confined.execute does. Fake agy
    (this test's own child); the sweep is a stand-in, so no process table is read."""
    seen = []

    def sweep(*_args, **kwargs):
        seen.append(kwargs.get("cwd_dirs"))
        return Sweep(listed=True)

    monkeypatch.setattr(agy_module, "sweep_marked", sweep)
    agy = fake_agy(tmp_path, "print('done', flush=True)")
    workdir = tmp_path / attempt(7)
    workdir.mkdir()
    AgyWorker(agy=agy).run(workdir, TASK)
    assert seen and set(seen) == {(os.path.realpath(workdir),)}


REAL_STOP = agy_module._stop
TIED_BY_CWD = Sweep(listed=True, killed=1, held_by=((4242, "cwd"),))  # killed, then gone


class _DoneReader(_Reader):
    def join(self, timeout=None):
        pass

    def is_alive(self):
        return False


def _with_agys_sweep(tmp_path, monkeypatch, dispatch, looks):
    """As _ending (exit 0), but agy's own _stop runs on a finished copier: its sweep answers
    ``looks`` in turn, then the last one again. Stand-ins only: the group kill is a no-op, no
    signal is sent and no process table is read."""
    _ending(tmp_path, monkeypatch, dispatch, 0, False)
    monkeypatch.setattr(agy_module, "_stop", REAL_STOP)
    monkeypatch.setattr(agy_module.os, "killpg", lambda *_a: None)
    answers = iter(looks)
    monkeypatch.setattr(agy_module, "sweep_marked", lambda *_a, **_k: next(answers, looks[-1]))
    copier = threading.Thread(target=lambda: None)
    copier.start()
    copier.join()
    worker = AgyWorker(dispatch=dispatch)
    reader = None if dispatch is None else _DoneReader()
    attempt_ = ("attempt-1", "", ("/clone",))
    return copier, lambda: worker._await(
        _Ended(0, False), copier, reader, attempt_, io.BytesIO(), tmp_path / "log"
    )


@pytest.mark.parametrize("dispatch", WORKERS, ids=["raw-agy", "owner-dispatch", "worker-account"])
def test_a_process_tied_to_the_clone_by_its_cwd_fences_the_run(tmp_path, monkeypatch, dispatch):
    """#537: a process whose environment macOS withholds, found in the clone by its working
    directory and killed (two later looks clean, so the sweep is gone and nothing holds the
    output any more), still fences the run: without the cwd look it would have outlived agy,
    so it counts like a holder. Every path, the worker account's settling included."""
    _copier, run = _with_agys_sweep(
        tmp_path, monkeypatch, dispatch, [TIED_BY_CWD, Sweep(listed=True)]
    )
    with pytest.raises(WorkerMayLive, match="^WORKER_SWEEP_UNCLEAN: ") as caught:
        run()
    assert caught.value.account == (getattr(dispatch, "run_as_user", None) or "")
    if dispatch is None or dispatch.run_as_user is None:
        assert "CWD: 1 process(es) in the clone" in str(caught.value)


@pytest.mark.parametrize("dispatch", WORKERS, ids=["raw-agy", "owner-dispatch", "worker-account"])
def test_a_clean_sweep_with_nothing_in_the_clone_completes(tmp_path, monkeypatch, dispatch):
    """The control: nothing tied by its working directory, two clean looks, a clean exit: agy's
    stop reports none in the clone and the run completes as before."""
    copier, run = _with_agys_sweep(tmp_path, monkeypatch, dispatch, [Sweep(listed=True)])
    held, swept, by_cwd = agy_module._stop(_Ended(0, False), copier, "attempt-1", None, "", ("/c",))
    assert (held, swept.gone, by_cwd) == (False, True, 0)
    run()


def test_a_worker_account_run_with_a_cwd_hit_is_fenced_whatever_its_receipts_say(
    tmp_path, monkeypatch
):
    """With agy_run_as_user, the settling of the run is told about the cwd hit too: receipts that
    report a problem of their own still end the run fenced, with the CWD reason, not as an
    ordinary worker-account failure. Stand-ins only, as above."""
    _copier, run = _with_agys_sweep(
        tmp_path, monkeypatch, WORKERS[2], [TIED_BY_CWD, Sweep(listed=True)]
    )
    monkeypatch.setattr(agy_module.agy_receipts, "problems", lambda _r: ["WORKER_PROBLEM: x"])
    with pytest.raises(WorkerMayLive, match="CWD") as caught:
        run()
    assert caught.value.account == WORKER


def test_a_process_tied_by_cwd_at_two_looks_counts_once(tmp_path, monkeypatch):
    """The same pid tied to the clone at two looks is one process."""
    copier, run = _with_agys_sweep(
        tmp_path, monkeypatch, None, [TIED_BY_CWD, TIED_BY_CWD, Sweep(listed=True)]
    )
    held, swept, by_cwd = agy_module._stop(_Ended(0, False), copier, "attempt-1", None, "", ("/c",))
    assert (held, swept.gone, by_cwd) == (False, True, 1)
