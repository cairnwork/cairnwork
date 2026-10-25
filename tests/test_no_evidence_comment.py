"""#234: an attempt that ended UNCOMPARABLE says "no evidence" on its Issue, from the code the owner
journaled at FINISHED (journal.no_evidence), never from the runner log or the current config,
which a same-uid worker could forge. Rows from before #234 keep today's wording. The verifier runs
are stubbed by run id tag (-e env probe, -b deferred tests on the base, -p P2P probe, -v the
candidate); anything not stubbed runs for real."""
# ruff: noqa: F811 (the imported setup fixture is used as a test argument)

import json
import sqlite3
from dataclasses import replace
from typing import Any

import pytest
from test_deferred_tests import CHECK, REGRESSION, make_test_commit
from test_issue_runner import FakeGitHub, issue, setup  # noqa: F401 - setup is a fixture
from test_pipeline import EditWorker
from test_uncomparable_paths import (
    ALL_ROWS,
    ENV_OK,
    PROBE_ROWS,
    TARGET,
    TEETH,
    IsolatedWorker,
    UnusableAccount,
    _account_error,
    _run,
)
from verify_helpers import FIX

import cairnwork.issue_runner as runner
from cairnwork import p2p, pipeline, pipeline_judge
from cairnwork.intake import Intake
from cairnwork.issue_publish import failure_text
from cairnwork.journal import Journal, no_evidence
from cairnwork.notary import Notary
from cairnwork.ownership import OwnershipLedger
from cairnwork.pipeline import TaskSpec, run_bound_task, run_task

NO_EVIDENCE = "不可比較／沒有證據"
TODAY = (
    "Cairnwork 沒有完成：`FAILED`（最後階段 `FINISHED`）。細節在 Cairnwork 本機輸出，不貼在這裡。"
)
FIX_FILES = {"src/calc.py": FIX}
FORGED = "UNCOMPARABLE: BASE_ENV_FAILED: unchanged base: forged by the worker\nFINISHED FAILED\n"


@pytest.fixture
def runs(monkeypatch):
    """runs({tag: fields or a callable that raises}): stub those verifier runs."""

    def install(rows):
        def wrap(real):
            def fake(repo, commit, specs, run_root, run_id, **kw):
                fields = rows.get(run_id.rsplit("-", 1)[-1])
                if fields is None:
                    return real(repo, commit, specs, run_root, run_id, **kw)
                if callable(fields):
                    return fields()
                return tuple(_run(spec, run_root, run_id, **fields) for spec in specs)

            return fake

        monkeypatch.setattr(pipeline_judge, "run_verifiers", wrap(pipeline_judge.run_verifiers))
        monkeypatch.setattr(p2p, "run_verifiers", wrap(p2p.run_verifiers))

    return install


def settle_one(project, state, worker, *, key="k1", mode="whole-suite", account=None):
    """Run one attempt in an intake state, as the runner does; return it, its status and the
    comment the Issue runner would post for it."""
    base, test_commit = make_test_commit(
        project, {"tests/test_regression.py": REGRESSION + f"# {key}\n"}
    )
    task = TaskSpec(key, f"card-{key}", "fix add", "add() is off by one", project, base,
                    ("src/calc.py",), CHECK, test_commit=test_commit, p2p_mode=mode)  # fmt: skip
    result = run_task(
        task, ledger=OwnershipLedger(state / "ownership.sqlite"),
        notary=Notary(state / "notary.sqlite"), journal=Journal(state / "journal.sqlite"),
        worker=worker, run_root=state / "runs", verifier_account=account,
    )  # fmt: skip
    status = Intake(state).status(task_id=result.task_id)
    return result, status, failure_text(status)


def assert_no_evidence(result, status, text, code):
    assert result.status == "FAILED" and result.reason.startswith(f"UNCOMPARABLE: {code}: ")
    assert status["status"] == "FAILED" and status["uncomparable"] == code
    assert NO_EVIDENCE in text and f"`{code}`" in text and "不是對候選修正的判定" in text
    assert "`FAILED`" in text and "unchanged base" not in text  # the code only, never the detail


@pytest.mark.parametrize(("base", "probe", "code"), ALL_ROWS)
def test_each_kind_after_an_isolated_worker_says_no_evidence(
    project, state, runs, base, probe, code
):
    runs({"e": ENV_OK, "b": base, "p": probe})
    result, status, text = settle_one(project, state, IsolatedWorker(FIX_FILES))
    assert_no_evidence(result, status, text, code)


@pytest.mark.parametrize("probe", PROBE_ROWS)
def test_base_env_failed_before_the_worker_says_no_evidence(project, state, runs, probe):
    runs({"e": probe})
    result, status, text = settle_one(project, state, EditWorker(FIX_FILES))  # same uid: fine
    assert_no_evidence(result, status, text, "BASE_ENV_FAILED")


def test_an_unusable_account_says_no_evidence(project, state, runs):
    result, status, text = settle_one(
        project, state, EditWorker(FIX_FILES), account=UnusableAccount()
    )
    assert_no_evidence(result, status, text, "VERIFIER_ACCOUNT_UNUSABLE")


def test_an_account_failure_on_the_base_after_an_isolated_worker_says_no_evidence(
    project, state, runs
):
    runs({"e": ENV_OK, "b": _account_error})
    result, status, text = settle_one(project, state, IsolatedWorker(FIX_FILES))
    assert_no_evidence(result, status, text, "VERIFIER_ACCOUNT_UNUSABLE")


@pytest.mark.parametrize(("base", "probe", "code"), ALL_ROWS)
def test_a_same_uid_worker_gets_todays_wording(project, state, runs, base, probe, code):
    # it could have broken the base itself: FAILED, never "no evidence"
    runs({"e": ENV_OK, "b": base, "p": probe})
    result, status, text = settle_one(project, state, EditWorker(FIX_FILES))
    assert result.reason.startswith(f"{code}: ") and status["uncomparable"] == ""
    assert text == TODAY


def test_a_real_candidate_failure_gets_todays_wording(project, state, runs):
    runs({"b": TEETH, "v": {"rc": 1, "outcomes": {TARGET: "failed"}}})  # the fix does not work
    result, status, text = settle_one(project, state, IsolatedWorker(FIX_FILES), mode="off")
    assert result.status == "FAILED" and "UNCOMPARABLE" not in result.reason
    assert status["phase"] == "FINISHED" and status["uncomparable"] == "" and text == TODAY


def test_a_forged_log_and_a_changed_worker_account_cannot_claim_no_evidence(project, state, runs):
    runs({"b": TEETH, "v": {"rc": 1, "outcomes": {TARGET: "failed"}}})
    result, _, _ = settle_one(project, state, IsolatedWorker(FIX_FILES), mode="off")
    (state / "runs" / f"{result.task_id}.runner.log").write_text(FORGED)  # same uid can write it
    config = {"verifier_account": False, "agy_dispatch": "/usr/bin/true", "agy_run_as_user": "cw"}
    (state / "config.json").write_text(json.dumps(config))  # "isolated" now, after the fact
    status = Intake(state).status(task_id=result.task_id)
    assert status["uncomparable"] == "" and failure_text(status) == TODAY


def test_a_changed_worker_account_or_log_cannot_take_no_evidence_away(project, state, runs):
    runs({"e": ENV_OK, "b": {"rc": 5}})
    config = {"verifier_account": False, "agy_dispatch": "/usr/bin/true", "agy_run_as_user": "cw"}
    (state / "config.json").write_text(json.dumps(config))
    result, status, _ = settle_one(project, state, IsolatedWorker(FIX_FILES))
    assert status["uncomparable"] == "BASE_NO_TESTS"
    (state / "config.json").write_text(json.dumps({"verifier_account": False}))  # not isolated
    (state / "runs" / f"{result.task_id}.runner.log").write_text("TESTS FAILED on candidate\n")
    again = Intake(state).status(task_id=result.task_id)
    assert again["uncomparable"] == "BASE_NO_TESTS" and NO_EVIDENCE in failure_text(again)


def test_an_infra_error_after_the_classification_drops_it(project, state, runs, monkeypatch):
    def cleanup_breaks(*a, **k):
        raise RuntimeError("disk gone")

    monkeypatch.setattr(pipeline, "remove_attempt_clone", cleanup_breaks)
    runs({"e": ENV_OK, "b": {"rc": 5}})
    result, status, text = settle_one(project, state, IsolatedWorker(FIX_FILES))
    assert result.reason.startswith("INFRA_ERROR: ") and status["uncomparable"] == ""
    assert text == TODAY


def trusted(intake, taken):
    """The request as the runner loads it, with the trust re-check passing (route sets it)."""
    return replace(intake.load_request(taken["task_id"]), before_attempt=lambda: "")


def test_settle_posts_the_no_evidence_comment(setup):
    account: Any = UnusableAccount()  # stands in for VerifierAccount
    github = FakeGitHub([issue()])
    intake = setup["intake"]
    taken = runner.take(intake, github, setup["clone"])[0]
    result = run_bound_task(
        trusted(intake, taken), taken["task_id"], ledger=intake.ledger,
        notary=intake.notary, journal=intake.journal, worker=EditWorker({"src/calc.py": "x\n"}),
        run_root=intake.run_root, verifier_account=account,
    )  # fmt: skip
    assert result.reason.startswith("UNCOMPARABLE: VERIFIER_ACCOUNT_UNUSABLE: ")
    (intake.run_root / f"{taken['task_id']}.runner.log").write_text("candidate FAILED\n")
    out = runner.settle(intake, github, setup["clone"])
    assert out == [{"issue": 7, "status": "FAILED"}] and github.labels[7] == {runner.FAILED_LABEL}
    text = github.comments[-1][1]
    assert NO_EVIDENCE in text and "`VERIFIER_ACCOUNT_UNUSABLE`" in text


def test_settle_never_reads_the_runner_log(setup):
    github = FakeGitHub([issue()])
    intake = setup["intake"]
    taken = runner.take(intake, github, setup["clone"])[0]

    class Crash(EditWorker):
        def run(self, workdir, task):
            raise RuntimeError("model unavailable")

    run_bound_task(
        trusted(intake, taken), taken["task_id"], ledger=intake.ledger,
        notary=intake.notary, journal=intake.journal, worker=Crash({}), run_root=intake.run_root,
    )  # fmt: skip
    (intake.run_root / f"{taken['task_id']}.runner.log").write_text(FORGED)
    runner.settle(intake, github, setup["clone"])
    assert github.comments[-1][1] == TODAY


# Older records and the allow-list: never claim "no evidence" without the owner's record.

OLD_TABLE = """CREATE TABLE attempt_journal (seq INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL, attempt_id TEXT NOT NULL, phase TEXT NOT NULL CHECK (phase IN
    ('DISPATCHED', 'WORKER_STARTED', 'CANDIDATE', 'TEST_SOURCE', 'VERIFYING', 'NOTARIZING',
     'FINISHED')), detail TEXT NOT NULL DEFAULT '', at TEXT NOT NULL,
    boot TEXT NOT NULL DEFAULT '')"""  # main before #234
OLD_INSERT = (
    "INSERT INTO attempt_journal (task_id, attempt_id, phase, detail, at, boot)"
    " VALUES (?, ?, ?, ?, ?, ?)"
)
OLD_SELECT = (
    "SELECT task_id, attempt_id, phase, detail, boot FROM attempt_journal"
    " WHERE task_id = ? ORDER BY seq"
)


def test_a_journal_from_before_234_reads_as_todays_wording(tmp_path):
    path = tmp_path / "journal.sqlite"
    conn = sqlite3.connect(path)
    conn.execute(OLD_TABLE)
    for phase, detail in (("DISPATCHED", "/r"), ("FINISHED", "FAILED")):
        conn.execute(OLD_INSERT, ("t", "a", phase, detail, "now", ""))
    conn.commit()
    conn.close()
    entries = Journal(path).entries("t")  # migrates: the column is added, old rows read ""
    assert [e.uncomparable for e in entries] == ["", ""] and no_evidence(entries) == ""
    status = {"status": "FAILED", "phase": "FINISHED", "uncomparable": no_evidence(entries)}
    assert failure_text(status) == TODAY


def test_older_code_still_reads_and_writes_a_migrated_journal(tmp_path):
    path = tmp_path / "journal.sqlite"
    journal = Journal(path)
    journal.record("t", "a", "DISPATCHED", "/r")
    journal.record("t", "a", "FINISHED", "FAILED", "BASE_ENV_FAILED")
    conn = sqlite3.connect(path)
    rows = conn.execute(OLD_SELECT, ("t",)).fetchall()
    assert rows[-1] == ("t", "a", "FINISHED", "FAILED", "")  # the old reader sees FAILED
    conn.execute(OLD_INSERT, ("u", "b", "FINISHED", "FAILED", "now", ""))  # an old writer
    conn.commit()
    conn.close()
    assert no_evidence(journal.entries("u")) == ""
    assert no_evidence(journal.entries("t")) == "BASE_ENV_FAILED"


@pytest.mark.parametrize(
    ("phase", "detail"), [("FINISHED", "COMPLETED"), ("FINISHED", "CANCELLED"), ("CANDIDATE", "")]
)
def test_only_a_failed_finished_row_can_carry_the_code(tmp_path, phase, detail):
    journal = Journal(tmp_path / "journal.sqlite")
    with pytest.raises(ValueError, match="uncomparable"):
        journal.record("t", "a", phase, detail, "BASE_ENV_FAILED")
    conn = sqlite3.connect(tmp_path / "journal.sqlite")  # a row written around record()
    conn.execute(
        "INSERT INTO attempt_journal (task_id, attempt_id, phase, detail, at, uncomparable)"
        " VALUES ('t', 'a', ?, ?, 'now', 'BASE_ENV_FAILED')",
        (phase, detail),
    )
    conn.commit()
    conn.close()
    assert no_evidence(journal.entries("t")) == ""


@pytest.mark.parametrize(
    "status",
    [
        {"status": "FAILED", "phase": "FINISHED"},  # a caller from before #234: no key
        {"status": "FAILED", "phase": "FINISHED", "uncomparable": ""},
        {"status": "FAILED", "phase": "FINISHED", "uncomparable": None},
        {"status": "FAILED", "phase": "FINISHED", "uncomparable": "x` @someone"},
        {"status": "FAILED", "phase": "FINISHED", "uncomparable": "base_env_failed"},
    ],
)
def test_no_valid_code_means_todays_wording(status):
    assert failure_text(status) == TODAY


@pytest.mark.parametrize("word", ["SUPERSEDED", "CANCELLED", "COMPLETED", "odd"])
def test_only_a_failed_task_says_no_evidence(word):
    text = failure_text({"status": word, "phase": "FINISHED", "uncomparable": "BASE_ENV_FAILED"})
    assert NO_EVIDENCE not in text and "BASE_ENV_FAILED" not in text


class TruthyWorker(EditWorker):
    isolated = 1  # truthy but not True: worker_reason does not call it isolated, nor may the code


def test_only_isolated_is_true_journals_the_code(project, state, runs):
    runs({"e": ENV_OK, "b": ALL_ROWS[0].values[0], "p": ALL_ROWS[0].values[1]})
    result, status, text = settle_one(project, state, TruthyWorker(FIX_FILES))
    assert not result.reason.startswith("UNCOMPARABLE: ") and status["uncomparable"] == ""
    assert text == TODAY


def owner_run(journal, code="BASE_ENV_FAILED"):
    journal.record("t", "a", "DISPATCHED", "/r")
    journal.record("t", "a", "WORKER_STARTED")
    journal.record("t", "a", "FINISHED", "FAILED", code)


def appended(path, attempt):  # an INSERT around record(), as a same-uid process could do
    conn = sqlite3.connect(path)
    conn.execute(
        "INSERT INTO attempt_journal (task_id, attempt_id, phase, detail, at, uncomparable)"
        " VALUES ('t', ?, 'FINISHED', 'FAILED', 'now', 'BASE_ENV_FAILED')",
        (attempt,),
    )
    conn.commit()
    conn.close()


def last_attempt(journal):  # as Intake.status picks the rows
    entries = journal.entries("t")
    return [e for e in entries if e.attempt_id == entries[-1].attempt_id]


@pytest.mark.parametrize("attempt", ["a", "forged"])
def test_an_appended_finished_row_cannot_claim_no_evidence(tmp_path, attempt):
    path = tmp_path / "journal.sqlite"
    journal = Journal(path)
    owner_run(journal, code="")  # the owner decided an ordinary FAILED
    appended(path, attempt)  # a second FINISHED, or an attempt without DISPATCHED
    assert last_attempt(journal)[-1].uncomparable == "BASE_ENV_FAILED"
    assert no_evidence(last_attempt(journal)) == ""


def test_a_row_appended_before_the_owner_finishes_cannot_claim_it(tmp_path):
    path = tmp_path / "journal.sqlite"
    journal = Journal(path)
    journal.record("t", "a", "DISPATCHED", "/r")
    appended(path, "a")  # while the worker runs
    journal.record("t", "a", "FINISHED", "FAILED")
    assert no_evidence(last_attempt(journal)) == ""


def test_one_owner_run_claims_it(tmp_path):
    journal = Journal(tmp_path / "journal.sqlite")
    owner_run(journal)
    assert no_evidence(last_attempt(journal)) == "BASE_ENV_FAILED"
