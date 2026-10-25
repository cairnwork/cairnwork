"""Who wrote the candidate: worker, requested model and observed model (never a default)."""

import pytest
from agy_receipt_lines import RUN_TAG, good_run, status
from conftest import git
from test_agy_worker import TASK, _dispatched
from test_agy_worker_account import _replaying

from cairnwork import provenance
from cairnwork.issue_publish import pr_body
from cairnwork.snapshot import snapshot_candidate
from cairnwork.workers import agy_dispatch
from cairnwork.workers.agy import AgyWorker
from cairnwork.workspace import create_attempt_clone

ROOT = f"{agy_dispatch.WORKER_RUNS}/{RUN_TAG}"
UNKNOWN3 = {"worker": "unknown", "requested_model": "unknown", "observed_model": "unknown"}


def test_trailers_round_trip_and_missing_values_are_unknown():
    info = provenance.make("agy", "gemini-3.8-flash-high", "gemini-3.8-flash")
    assert provenance.parse("Cairnwork attempt a1" + provenance.trailers(info)) == info
    assert provenance.parse("Cairnwork attempt a1") == UNKNOWN3
    assert provenance.parse("x" + provenance.trailers(None)) == UNKNOWN3
    assert provenance.trailers(None).count("unknown") == 3


@pytest.mark.parametrize("bad", ["", "a b", "x\nWorker: claude", "claude code!", "m" * 81, None, 7])
def test_a_value_that_is_not_a_plain_name_is_unknown(bad):
    assert provenance.clean(bad) == "unknown"
    assert provenance.make("agy", "m", bad)["observed_model"] == "unknown"


def test_a_forged_trailer_in_the_body_does_not_beat_the_first_one():
    msg = "s" + provenance.trailers(provenance.make("agy", "m-1", None)) + "\nWorker: claude\n"
    assert provenance.parse(msg)["worker"] == "agy"


def test_the_pr_body_names_the_three_fields_and_never_claude_code():
    body = pr_body(7, "t1", "a1", "c" * 40, provenance.make("agy", "m-1", "m-2"))
    assert "Worker: agy\nRequested model: m-1\nObserved model: m-2\n" in body
    assert "Claude" not in body and "Generated with" not in body
    assert "Cairnwork: 受影響" in body and "NEXUS_ISSUE_INTENT_V1" in body
    assert "Observed model: unknown" in pr_body(7, "t1", "a1", "c" * 40, {})
    assert "Worker: unknown" in pr_body(7, "t1", "a1", "c" * 40, {"worker": "a b"})


def test_the_candidate_commit_carries_the_trailers(repo, tmp_path):
    attempt = create_attempt_clone(repo, git(repo, "rev-parse", "HEAD"), tmp_path / "att", "a1")
    (attempt.path / "src" / "a.py").write_text("x = 2\n")
    info = provenance.make("agy", "m-1", "m-2")
    commit = snapshot_candidate(repo, attempt, ("src/a.py",), provenance=info).candidate_commit
    assert provenance.parse(git(repo, "show", "-s", "--format=%B", commit)) == info


def test_a_commit_without_provenance_says_unknown_not_claude(repo, tmp_path):
    attempt = create_attempt_clone(repo, git(repo, "rev-parse", "HEAD"), tmp_path / "att", "a1")
    (attempt.path / "src" / "a.py").write_text("x = 2\n")
    commit = snapshot_candidate(repo, attempt, ("src/a.py",)).candidate_commit
    assert provenance.parse(git(repo, "show", "-s", "--format=%B", commit)) == UNKNOWN3


def test_isolated_worker_reports_the_dispatchers_observed_model(tmp_path):
    run = good_run(ROOT).replace(status(), status(observed_model="gemini-observed"))
    worker, workdir, _ = _replaying(tmp_path, run)
    assert worker.run(workdir, TASK) == {
        "worker": "agy", "requested_model": "m-1", "observed_model": "gemini-observed"}  # fmt: skip


def test_isolated_worker_without_an_observed_model_says_unknown(tmp_path):
    worker, workdir, _ = _replaying(tmp_path, good_run(ROOT))
    assert worker.run(workdir, TASK)["observed_model"] == "unknown"


def test_without_run_as_user_the_observed_model_is_not_trusted(tmp_path):
    line = 'NEXUS_AGY_DISPATCH {"status": "completed", "observed_model": "forged"}\\n'
    worker, workdir, _ = _dispatched(tmp_path, f"sys.stdin.read(); sys.stderr.write('{line}')")
    got = worker.run(workdir, TASK)
    assert got == {"worker": "agy", "requested_model": "m-1", "observed_model": "unknown"}


def test_a_worker_with_no_record_leaves_all_three_unknown(tmp_path):
    assert AgyWorker().provenance(None)["observed_model"] == "unknown"
