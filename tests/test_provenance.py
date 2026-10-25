"""Who wrote the candidate: worker, requested model and observed model (never a default)."""

import time

import pytest
from agy_receipt_lines import RUN_TAG, cleanup, good_run, launch
from conftest import commit_change, git
from test_agy_worker import TASK, attempt, fake_dispatcher

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
    assert provenance.parse("Cairnwork attempt a1" + provenance.trailers(None)) == UNKNOWN3
    assert provenance.parse("x" + provenance.trailers(info)) == UNKNOWN3
    assert provenance.trailers(None).count("unknown") == 3


@pytest.mark.parametrize("bad", ["", "a b", "x\nWorker: claude", "claude code!", "m" * 81, None, 7])
def test_a_value_that_is_not_a_plain_name_is_unknown(bad):
    assert provenance.clean(bad) == "unknown"
    assert provenance.make("agy", "m", bad)["observed_model"] == "unknown"


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


H = "Cairnwork attempt a1"
T = "Worker: agy\nRequested-Model: m-1\nObserved-Model: m-2"
GOOD = f"{H}\n\n{T}\n"
WANT = provenance.make("agy", "m-1", "m-2")


def test_the_exact_shapes_cairnwork_writes_are_read():
    assert provenance.parse(GOOD) == WANT
    assert provenance.parse(GOOD.rstrip("\n")) == WANT
    assert provenance.parse(GOOD + "\n\n") == WANT  # only newlines may trail
    tested = f"{H}: tests from {'a' * 40}\n\n{T}\n"  # deferred_tests.lay_tests_over
    assert provenance.parse(tested) == WANT


@pytest.mark.parametrize("message", [
    f"{H}\n\nsome prose\n{T}\n",  # prose in the block
    f"{H}\n\n{T}\n\nand then a paragraph\n",
    f"{H}\n\nbody paragraph\n\n{T}\n",  # a body paragraph before the block
    f"{H}\nmore subject\n\n{T}\n",  # multi-line subject
    f"{H}\n\n\n{T}\n",  # extra blank line
    f"Worker: claude\n\n{GOOD}",  # an earlier block
    f"{H}\n\n{T}\n\n{T}\n",  # the block twice
    GOOD.replace("\n", "\r\n"),  # CRLF
    f"{H}\r\n\n{T}\n",  # CR in the subject only
    f"{H}\r\n\nWorker: agy\nRequested-Model: {'m' * 70}\nObserved-Model: {'n' * 70}\n",  # far
    f"{H}\n\r\n{T}\n",  # mixed endings
    f"{H}\n\n{T.replace(chr(10), chr(13) + chr(10), 1)}\n",
    f"{H}\n  \n{T}\n",  # blank holds spaces
    f"\n\n{T}\n",  # no subject
    f"{T}\n",  # only trailers
    f"  \n\n{T}\n",  # blank subject
    f"s\n\n{T}\n",  # not a Cairnwork attempt subject
    f"cairnwork attempt a1\n\n{T}\n",
    f"Worker: agy\n\n{T}\n",  # a subject that is itself a trailer line
    GOOD + "Signed-off-by: x <x@y>\n",  # extra trailer after
    GOOD.replace("\nWorker", "\nSigned-off-by: x <x@y>\nWorker"),  # extra trailer before
    f"{H}\n\nRequested-Model: m-1\nWorker: agy\nObserved-Model: m-2\n",  # out of order
    GOOD + "Observed-Model: other\n",  # duplicate key
    GOOD + "  \n",  # only newlines may trail, not spaces
    GOOD.rstrip("\n") + "\r",
    f"{H}\n\nWorker: agy\nWorker: claude\nRequested-Model: m-1\nObserved-Model: m-2\n",
    f"{H}\n\nWorker: agy\nRequested-Model: m-1\n",  # missing key
    "",
])  # fmt: skip
def test_any_other_shape_makes_all_three_unknown(message):
    assert provenance.parse(message) == UNKNOWN3


def test_trailing_whitespace_on_a_value_makes_that_value_unknown():
    msg = f"{H}\n\nWorker: agy \nRequested-Model: m-1\nObserved-Model: m-2\n"
    assert provenance.parse(msg) == {**WANT, "worker": "unknown"}


@pytest.mark.parametrize("how", ["commit", "commit-tree"])
def test_the_shape_survives_a_real_git_commit_and_the_read_back(repo, how):
    info = provenance.make("agy", "gemini-3.8-flash-high", "gemini-3.8-flash")
    message = "Cairnwork attempt a1: tests from abc" + provenance.trailers(info)
    if how == "commit":  # git's own message cleanup runs here
        commit = commit_change(repo, {"f.txt": "x\n"}, message=message)
    else:
        tree = git(repo, "write-tree")
        commit = git(repo, "commit-tree", tree, "-p", "HEAD", "-m", message)
    # the read-back issue_runner._open_pr does
    assert provenance.parse(git(repo, "show", "-s", "--format=%B", commit)) == info


OP = "agyop_" + "ab" * 16
OP2 = "agyop_" + "cd" * 16
# the shape of `nexus-agy-dispatch --status <id>`: the whole operation record
RECORD = {"schema": "nexus.agy_operation.v1", "status": "COMPLETED", "phase": "TERMINAL",
          "requested_model": "m-1", "resolved_model": "m-1",
          "observed_model": "gemini-3.8-flash-high",
          "account_alias_hash": "63ef3e58e241", "failure_kind": None}  # fmt: skip


def _stub(tmp_path, records, *, need_env=None, sleep=0):
    """A dispatcher whose --status=<id> prints that id's record (with operation_id echoed)."""
    guard = (
        f"if os.environ.get({need_env[0]!r}) != {need_env[1]!r}: sys.exit(3)\n" if need_env else ""
    )
    body = (
        "arg = [a for a in sys.argv if a.startswith('--status=')][0][9:]\n"
        f"import time; time.sleep({sleep})\n{guard}"
        f"rec = {records!r}.get(arg)\n"
        "if rec is None: sys.exit(1)\n"
        "print(json.dumps({**rec, 'operation_id': rec.get('operation_id', arg)}))"
    )
    return agy_dispatch.Dispatch(fake_dispatcher(tmp_path, body), run_as_user="w")


class _Reader:
    def __init__(self, *lines):
        self.lines_ = list(lines)

    def lines(self):
        return self.lines_

    def status(self):
        return {"status": "completed", "observed_model": "from-the-stderr-line"}


def _launch(op, attempt=1):
    return launch(ROOT, attempt=attempt).replace('"operation_id": null', f'"operation_id": "{op}"')


def _observed(dispatch, *lines):
    worker = AgyWorker(model="m-1", dispatch=dispatch)
    return worker.provenance(_Reader(*lines))["observed_model"]


def test_observed_model_comes_from_the_dispatcher_status_of_the_launch_operation(tmp_path):
    dispatch = _stub(tmp_path, {OP: RECORD})
    assert _observed(dispatch, _launch(OP)) == "gemini-3.8-flash-high"


def test_the_stderr_status_line_is_never_a_source(tmp_path):
    dispatch = _stub(tmp_path, {})  # --status finds nothing; the reader's line has a value
    assert _observed(dispatch, _launch(OP)) == "unknown"
    assert _observed(dispatch) == "unknown"  # no launch receipt at all


@pytest.mark.parametrize("record", [
    {**RECORD, "status": "RUNNING"}, {**RECORD, "status": "FAILED"},
    {**RECORD, "status": "OUTCOME_UNKNOWN"}, {**RECORD, "operation_id": OP2},
    {"status": "COMPLETED"}, {**RECORD, "observed_model": "bad name!"},
    {**RECORD, "observed_model": None},
])  # fmt: skip
def test_a_record_that_is_not_a_finished_operation_with_a_usable_model_is_unknown(tmp_path, record):
    assert _observed(_stub(tmp_path, {OP: record}), _launch(OP)) == "unknown"


def test_the_attempt_that_produced_the_candidate_is_the_last_one_that_launched(tmp_path):
    other = {**RECORD, "observed_model": "first-attempt-model"}
    dispatch = _stub(tmp_path, {OP: other, OP2: RECORD})
    two = (_launch(OP, 1), _launch(OP2, 2))
    assert _observed(dispatch, *two) == "gemini-3.8-flash-high"
    never_launched = cleanup(attempt=3)  # a later attempt with no launch receipt
    assert _observed(dispatch, *two, never_launched) == "gemini-3.8-flash-high"
    assert _observed(dispatch, _launch(OP, 1), cleanup(attempt=2)) == "first-attempt-model"


@pytest.mark.parametrize("op", ["x" + OP, OP + "y", OP.upper(), "--evil", "", None, 7])
def test_an_operation_id_that_is_not_exactly_an_operation_id_is_never_queried(tmp_path, op):
    ops = {str(op): RECORD, "x" + OP: RECORD, OP + "y": RECORD}
    assert _observed(_stub(tmp_path, ops), _launch(op)) == "unknown"


def test_a_hanging_status_query_times_out_to_unknown(tmp_path, monkeypatch):
    monkeypatch.setattr(agy_dispatch, "STATUS_QUERY_SECONDS", 0.5)
    started = time.monotonic()
    got = _observed(_stub(tmp_path, {OP: RECORD}, sleep=6), _launch(OP))
    assert got == "unknown" and time.monotonic() - started < 4


def test_the_status_query_sees_the_dispatchers_environment_and_nothing_more(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_AGY_OPERATION_ROOT", "/ops")
    monkeypatch.setenv("OWNER_ONLY_SECRET", "s")
    seen = _stub(tmp_path, {OP: RECORD}, need_env=("NEXUS_AGY_OPERATION_ROOT", "/ops"))
    assert _observed(seen, _launch(OP)) == "gemini-3.8-flash-high"
    hidden = _stub(tmp_path, {OP: RECORD}, need_env=("OWNER_ONLY_SECRET", "s"))
    assert _observed(hidden, _launch(OP)) == "unknown"


def test_without_run_as_user_the_observed_model_is_not_asked_for(tmp_path):
    dispatch = _stub(tmp_path, {OP: RECORD})
    plain = agy_dispatch.Dispatch(dispatch.path)
    assert _observed(plain, _launch(OP)) == "unknown"


def test_a_worker_with_no_record_leaves_all_three_unknown(tmp_path):
    assert AgyWorker().provenance(None)["observed_model"] == "unknown"


def test_a_whole_run_reports_the_model_of_the_operation_it_launched(tmp_path):
    run = good_run(ROOT).replace('"operation_id": null', f'"operation_id": "{OP}"')
    body = (
        "if any(a.startswith('--status=') for a in sys.argv):\n"
        f"    print(json.dumps({{**{RECORD!r}, 'operation_id': {OP!r}}})); sys.exit(0)\n"
        f"sys.stdin.read(); sys.stderr.write({run!r})"
    )
    dispatch = agy_dispatch.Dispatch(
        fake_dispatcher(tmp_path, body), run_as_user="_cairnwork_worker"
    )
    workdir = tmp_path / attempt(1)
    workdir.mkdir()
    got = AgyWorker(model="m-1", dispatch=dispatch).run(workdir, TASK)
    assert got == {"worker": "agy", "requested_model": "m-1",
                   "observed_model": "gemini-3.8-flash-high"}  # fmt: skip
