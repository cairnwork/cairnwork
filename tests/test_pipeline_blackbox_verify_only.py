"""A blackbox declaration is for verify only: a worker run refuses it together with deferred
tests, before the task is bound or the worker starts."""

import secrets
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest
from conftest import commit_change, git

from cairnwork.blackbox import Case, Check, Declaration
from cairnwork.journal import Journal
from cairnwork.notary import Notary
from cairnwork.ownership import OWNER, OwnershipLedger, OwnershipUnknown
from cairnwork.pipeline import TaskRejected, TaskSpec, check_task, run_bound_task, run_task
from cairnwork.workers import WorkerDefaults

DECLARED = Declaration(
    secrets.token_hex(32), (Check("add", "calc", "add", (Case("c1", "[2,3]", "{}", "5"),)),)
)
VERIFY = (("pytest", ("python3", "-m", "pytest", "-q", "tests/test_calc.py")),)


class CountingWorker(WorkerDefaults):
    def __init__(self):
        self.calls = 0

    def run(self, workdir: Path, task) -> None:
        self.calls += 1


@pytest.fixture
def both(repo):
    commit_change(repo, {"src/calc.py": "def add(a, b):\n    return a + b\n"})
    return TaskSpec(
        "k1", "card-k1", "fix add", "add", repo, git(repo, "rev-parse", "HEAD"),
        ("src/calc.py",), VERIFY, test_commit="a" * 40, blackbox=DECLARED,
    )  # fmt: skip


def stores(tmp_path):
    return (
        OwnershipLedger(tmp_path / "own.sqlite"),
        Notary(tmp_path / "notary.sqlite"),
        Journal(tmp_path / "journal.sqlite"),
    )


def journal_rows(tmp_path):
    with sqlite3.connect(tmp_path / "journal.sqlite") as conn:
        return conn.execute("SELECT phase FROM attempt_journal").fetchall()


def test_check_task_still_accepts_both_for_verify(both):
    assert check_task(both)


def test_run_task_refuses_both_before_the_worker_starts(both, tmp_path):
    ledger, notary, journal = stores(tmp_path)
    worker = CountingWorker()
    with pytest.raises(TaskRejected) as caught:
        run_task(both, ledger=ledger, notary=notary, journal=journal, worker=worker,
                 run_root=tmp_path / "runs")  # fmt: skip
    assert caught.value.code == "BAD_BLACKBOX" and "verify only" in str(caught.value)
    assert worker.calls == 0 and journal_rows(tmp_path) == []
    with pytest.raises(OwnershipUnknown):
        ledger.get(idempotency_key="k1")


def test_run_bound_task_refuses_both_before_the_worker_starts(both, tmp_path):
    ledger, notary, journal = stores(tmp_path)
    bound = ledger.bind(both.idempotency_key, both.request(), OWNER, both.card_hash)
    worker = CountingWorker()
    with pytest.raises(TaskRejected) as caught:
        run_bound_task(both, bound.record.task_id, ledger=ledger, notary=notary, journal=journal,
                       worker=worker, run_root=tmp_path / "runs")  # fmt: skip
    assert caught.value.code == "BAD_BLACKBOX"
    assert worker.calls == 0 and journal_rows(tmp_path) == []
    assert ledger.get(task_id=bound.record.task_id).status == "BOUND"


@pytest.mark.parametrize("field", [{"blackbox": None}, {"test_commit": ""}])
def test_either_declaration_alone_is_not_refused(both, tmp_path, field):
    ledger, notary, journal = stores(tmp_path)
    task = replace(both, **field)
    worker = CountingWorker()
    try:
        run_task(task, ledger=ledger, notary=notary, journal=journal, worker=worker,
                 run_root=tmp_path / "runs")  # fmt: skip
    except TaskRejected as exc:
        assert exc.code != "BAD_BLACKBOX"
