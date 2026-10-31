"""#537: a run ended by KeyboardInterrupt or SystemExit kills its verifier's process group and
keeps its directories: restart recovery ties a leftover process to the attempt by its working
directory (proc_cwd.attempt_dirs lists only directories that exist) and removes them after its
sweep. A normal return or an ordinary exception still removes them at once. Owner mode; a
verifier account's checkout: test_interrupt_account_mode.py. Stand-ins only: no process is
started or signalled here."""

import io
from pathlib import Path
from typing import Any

import pytest
from conftest import commit_change

import cairnwork.blackbox_exec as blackbox_exec
import cairnwork.confined as confined
import cairnwork.pipeline as pipeline
import cairnwork.verifiers as verifiers
from cairnwork.journal import Journal
from cairnwork.notary import Notary
from cairnwork.ownership import OwnershipLedger
from cairnwork.verifier_spec import VerifierSpec
from cairnwork.verify_context import VerifyContext
from cairnwork.workers import WorkerDefaults

STOPS = [KeyboardInterrupt(), SystemExit(3)]
ENDS = [*STOPS, RuntimeError("ordinary")]


def _kept(exc: BaseException) -> bool:
    return not isinstance(exc, Exception)


@pytest.mark.parametrize("stop", STOPS, ids=type)
def test_an_interrupt_in_the_wait_still_kills_the_process_group(monkeypatch, tmp_path, stop):
    killed = []

    class Stub:  # Popen whose first wait (the one with the timeout) is interrupted
        pid = 424242
        returncode = None

        def __init__(self, *args, **kwargs):
            self.stdout = io.BytesIO(b"")

        def wait(self, timeout=None):
            if timeout is not None:
                raise stop
            self.returncode = -9
            return -9

    monkeypatch.setattr(confined.subprocess, "Popen", Stub)
    monkeypatch.setattr(confined, "_kill_group", killed.append)
    env = {confined.RUN_TOKEN_VAR: "t"}
    with pytest.raises(type(stop)):
        confined.execute(["x"], tmp_path, env, 5, tmp_path / "log")
    assert killed == [Stub.pid]


def _raise(exc):
    def raiser(*args, **kwargs):
        raise exc

    return raiser


def _fill_private_then_raise(exc):
    def fake(*args):
        private = next(a for a in args if isinstance(a, Path) and ".work" not in str(a))
        (private / "run").mkdir()
        raise exc

    return fake


@pytest.fixture
def ctx(repo, tmp_path):
    head = commit_change(repo, {"tests/test_a.py": "def test_x():\n    pass\n"})
    return VerifyContext(repo, tmp_path / "runs", base_commit=head), head


SPEC = (VerifierSpec("v", ("tests/test_a.py",)),)


@pytest.mark.parametrize("end", ENDS, ids=type)
def test_verifier_checkout_is_kept_only_on_an_interrupt(monkeypatch, ctx, end):
    context, head = ctx
    monkeypatch.setattr(verifiers, "_run_one", _raise(end))
    with pytest.raises(type(end)):
        verifiers.run_verifiers(context, head, SPEC, "a1-v")
    assert (context.run_root / "a1-v-v0.work").exists() is _kept(end)


@pytest.mark.parametrize("end", ENDS, ids=type)
def test_verifier_scratch_is_kept_only_on_an_interrupt(monkeypatch, ctx, end):
    context, head = ctx
    monkeypatch.setattr(verifiers, "_run_one_in", _fill_private_then_raise(end))
    with pytest.raises(type(end)):
        verifiers.run_verifiers(context, head, SPEC, "a1-v")
    (private,) = [p for p in context.run_root.glob("a1-v-v0.*") if p.suffix != ".work"]
    assert (private / "run").exists() is _kept(end)


DECLARATION: Any = object()  # never read: the run below it is replaced


@pytest.mark.parametrize("end", ENDS, ids=type)
def test_blackbox_checkout_is_kept_only_on_an_interrupt(monkeypatch, ctx, end):
    context, head = ctx
    monkeypatch.setattr(blackbox_exec, "_run_in", _raise(end))
    with pytest.raises(type(end)):
        blackbox_exec.run_blackbox(DECLARATION, context, head, "a1-v")
    assert (context.run_root / "a1-v-vbb.work").exists() is _kept(end)


@pytest.mark.parametrize("end", ENDS, ids=type)
def test_blackbox_scratch_is_kept_only_on_an_interrupt(monkeypatch, ctx, end):
    context, head = ctx
    monkeypatch.setattr(blackbox_exec, "_run_in_open", _fill_private_then_raise(end))
    with pytest.raises(type(end)):
        blackbox_exec.run_blackbox(DECLARATION, context, head, "a1-v")
    (private,) = [p for p in context.run_root.glob("a1-v-vbb.*") if p.suffix != ".work"]
    assert (private / "run").exists() is _kept(end)


class EditWorker(WorkerDefaults):
    def run(self, workdir: Path, task) -> None:
        (workdir / "src" / "a.py").write_text("x = 2\n")


@pytest.mark.sandbox
@pytest.mark.parametrize("end", ENDS, ids=type)
def test_attempt_clone_is_kept_only_on_an_interrupt(monkeypatch, repo, tmp_path, end):
    monkeypatch.setattr(pipeline, "snapshot_candidate", _raise(end))
    head = commit_change(repo, {"tests/test_a.py": "def test_x():\n    pass\n"})
    verify = (("pytest", ("python3", "-m", "pytest", "-q", "tests/test_a.py")),)
    task = pipeline.TaskSpec("k1", "card-k1", "t", "d", repo, head, ("src/a.py",), verify)
    runs = tmp_path / "runs"

    def go():
        return pipeline.run_task(
            task,
            ledger=OwnershipLedger(tmp_path / "own.sqlite"),
            notary=Notary(tmp_path / "notary.sqlite"),
            journal=Journal(tmp_path / "journal.sqlite"),
            worker=EditWorker(),
            run_root=runs,
        )

    if _kept(end):
        with pytest.raises(type(end)):
            go()
    else:  # an ordinary exception after binding is the run's own FAILED (INFRA_ERROR)
        assert go().status == "FAILED"
    clones = [p for p in runs.iterdir() if p.is_dir() and "." not in p.name and "-" not in p.name]
    assert bool(clones) is _kept(end), sorted(p.name for p in runs.iterdir())
