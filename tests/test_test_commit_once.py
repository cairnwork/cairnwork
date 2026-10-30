"""A deferred test commit is consumed once across verify runs and worker runs: both paths share
one exclusive record under the state directory (deferred_store.consume)."""

import pytest
from conftest import git
from test_deferred_tests import CHECK, FIX, REGRESSION, SeeingWorker, make_test_commit
from test_pipeline import CrashWorker
from verify_helpers import candidate, verify

from cairnwork.deferred_store import DeferredStore
from cairnwork.journal import Journal
from cairnwork.notary import Notary
from cairnwork.ownership import OwnershipLedger
from cairnwork.pipeline import TaskSpec, run_task


def run(state, project, base, test_commit, worker, key):
    task = TaskSpec(key, "card-" + key, "fix add", "add() is off by one " + key, project, base,
                    ("src/calc.py",), CHECK, test_commit=test_commit,
                    test_store=DeferredStore.for_state(state, None))  # fmt: skip
    return run_task(
        task,
        ledger=OwnershipLedger(state / "own.sqlite"),
        notary=Notary(state / "notary.sqlite"),
        journal=Journal(state / "journal.sqlite"),
        worker=worker,
        run_root=state / "runs",
    )


def supersede(state, task_id):
    OwnershipLedger(state / "own.sqlite").set_status(task_id, "SUPERSEDED")


@pytest.mark.sandbox
def test_a_test_commit_is_consumed_once_across_verify_and_worker_runs(project, state):
    base, test_commit = make_test_commit(project, {"tests/test_regression.py": REGRESSION})
    cand = candidate(project, base, FIX)
    _code, out = verify(
        state, project, base, cand, "--test-commit", test_commit, tests=(" ".join(CHECK[0][1]),)
    )
    assert out["tests_consumed"] is True, out  # whatever the verdict, the run used the tests
    assert test_commit not in git(project, "rev-list", "--all")  # verify used a scratch clone
    worker = SeeingWorker(FIX)
    result = run(state, project, base, test_commit, worker, "k1")
    assert result.status == "FAILED"
    assert result.reason.startswith("TEST_COMMIT_ALREADY_EXPOSED")
    assert not hasattr(worker, "saw_test")  # refused before any worker started


class RacingWorker(SeeingWorker):
    """While this worker runs, a second task with the same test commit runs to its end."""

    def __init__(self, files, rival):
        super().__init__(files)
        self.rival, self.rival_result = rival, None

    def run(self, workdir, task) -> None:
        super().run(workdir, task)
        self.rival_result = self.rival()


def test_two_tasks_with_one_test_commit_only_one_gets_it(project, state):
    base, test_commit = make_test_commit(project, {"tests/test_regression.py": REGRESSION})
    racer = RacingWorker(
        FIX, lambda: run(state, project, base, test_commit, SeeingWorker(FIX), "k2")
    )  # the first task is past its pre-check when the second one starts
    first = run(state, project, base, test_commit, racer, "k1")
    assert racer.rival_result is not None
    assert racer.rival_result.status == "COMPLETED", racer.rival_result.reason
    assert first.status == "FAILED"
    assert first.reason.startswith("TEST_COMMIT_ALREADY_EXPOSED")
    assert (state / "verify-used-tests" / test_commit).exists()


def test_a_retry_after_a_worker_failure_keeps_the_unused_test_commit(project, state):
    base, test_commit = make_test_commit(project, {"tests/test_regression.py": REGRESSION})
    first = run(state, project, base, test_commit, CrashWorker(), "k1")
    assert first.reason.startswith("WORKER_FAILED")
    assert not (state / "verify-used-tests" / test_commit).exists()  # the tests never left
    supersede(state, first.task_id)
    second = run(state, project, base, test_commit, SeeingWorker(FIX), "k2")
    assert second.status == "COMPLETED", second.reason
    third = run(state, project, base, test_commit, SeeingWorker(FIX), "k3")
    assert third.reason.startswith("TEST_COMMIT_ALREADY_EXPOSED")  # now it is used
