"""Issue #1105 point 1: bounded verifier evidence is record-only."""

from types import SimpleNamespace

from cairnwork import failure_evidence
from cairnwork.core_request import VerifierObservation


def _run(path, outcomes, targets=("tests/test_x.py",), passed=False):
    return SimpleNamespace(
        spec=SimpleNamespace(verifier_id="pytest-0", targets=targets),
        outcomes=outcomes,
        log_path=path,
        observation=VerifierObservation("pytest-0", "artifact", "sha256:fixed", passed),
    )


def test_failed_nodeid_and_log_tail_are_recorded(tmp_path):
    path = tmp_path / "verifier.log"
    path.write_text("FAILED tests/test_recall.py::test_budget\n1509 > 1500\n")
    got = failure_evidence.capture(
        [_run(path, {"tests/test_recall.py::test_budget": "failed"})], home=str(tmp_path)
    )[0]
    assert got["failed_nodeids"] == ["tests/test_recall.py::test_budget"]
    assert "1509 > 1500" in got["log_tail"]


def test_nodeids_are_deduplicated_sorted_and_capped(tmp_path):
    path = tmp_path / "log"
    path.write_text("tail")
    outcomes = {f"tests/test_{i:02}.py::test_a": "failed" for i in range(23)}
    outcomes["tests/test_00.py::test_a"] = "failed"
    got = failure_evidence.capture([_run(path, outcomes)], home="")[0]
    assert got["failed_nodeids"] == sorted(outcomes)[:20]
    assert got["failed_nodeids_truncated"] is True


def test_tail_is_limited_by_lines_and_redacted(tmp_path):
    path = tmp_path / "verifier.log"
    path.write_text(
        "".join(f"line {i}\n" for i in range(250))
        + f"{tmp_path}/secret https://user:pass@example.test Bearer abc\n"
    )
    got = failure_evidence.capture([_run(path, {"n": "failed"})], home=str(tmp_path))[0]
    assert got["log_tail_truncated"] is True
    assert len(got["log_tail"].splitlines()) <= 200
    assert len(got["log_tail"].encode()) <= 16 * 1024
    assert "<home>/secret" in got["log_tail"]
    assert "user:pass" not in got["log_tail"] and "Bearer abc" not in got["log_tail"]


def test_tail_is_limited_by_bytes(tmp_path):
    path = tmp_path / "verifier.log"
    path.write_text("x" * (20 * 1024))
    got = failure_evidence.capture([_run(path, {"n": "failed"})], home="")[0]
    assert len(got["log_tail"].encode()) <= 16 * 1024
    assert got["log_tail_truncated"] is True


def test_hidden_tests_keep_no_tail_and_only_a_count(tmp_path):
    path = tmp_path / "verifier.log"
    path.write_text("secret hidden test output")
    outcomes = {
        "tests/test_hidden.py::test_secret": "failed",
        "tests/test_open.py::test_a": "failed",
    }
    targets = [
        ("tests/test_hidden.py",),
        (),  # whole suite
        ("tests/../tests/test_hidden.py",),
        ("Tests/Test_Hidden.py",),
        ("tests",),
        ("-k", "secret"),
    ]
    for t in targets:
        got = failure_evidence.capture(
            [_run(path, outcomes, t)],
            home="",
            deferred_paths=("tests/test_hidden.py",),
            hidden=True,
        )[0]
        assert got["log_tail_withheld"] is True and got["log_tail"] == "", t
        assert got["failed_nodeids"] == ["tests/test_open.py::test_a"], t
        assert got["failed_hidden_count"] == 1, t


def test_an_open_target_keeps_its_tail_even_with_hidden_tests(tmp_path):
    path = tmp_path / "verifier.log"
    path.write_text("open output")
    got = failure_evidence.capture(
        [_run(path, {"tests/test_open.py::test_a": "failed"}, ("tests/test_open.py",))],
        home="", deferred_paths=("tests/test_hidden.py",), hidden=True,
    )[0]  # fmt: skip
    assert got["log_tail"] == "open output" and not got["log_tail_withheld"]


def test_without_deferred_paths_everything_counts_as_hidden(tmp_path):
    path = tmp_path / "verifier.log"
    path.write_text("x")
    run = _run(path, {"tests/test_a.py::test_a": "failed"})
    got = failure_evidence.capture([run], home="", deferred_paths=(), hidden=True)[0]
    assert got["log_tail_withheld"] and got["failed_nodeids"] == []
    assert got["failed_hidden_count"] == 1


def test_missing_log_has_explicit_unavailable_marker(tmp_path):
    got = failure_evidence.capture([_run(tmp_path / "missing", {"n": "failed"})], home="")[0]
    assert got["log_tail"] == "" and got["log_tail_unavailable"] is True


def test_a_capture_error_becomes_a_marker_not_an_exception(tmp_path):
    run = _run(tmp_path / "missing", {"n": "failed"})
    run.outcomes = None  # malformed: capture itself raises
    task = SimpleNamespace(test_commit="")
    got = failure_evidence.capture_for_task([run], task, ())
    assert got == [
        {"id": "", "log_tail_unavailable": True, "failure_evidence_error": "AttributeError"}
    ]
