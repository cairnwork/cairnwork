"""Issue #1137 follow-up: evidence capture cost stays bounded for any ids or log."""

import time
from types import SimpleNamespace

from cairnwork import failure_evidence
from cairnwork.core_request import VerifierObservation

LIMIT_SECONDS = 1.0


def _run(path, outcomes):
    return SimpleNamespace(
        spec=SimpleNamespace(verifier_id="pytest-0", targets=("tests/test_x.py",)),
        outcomes=outcomes,
        collect_errors=(),
        log_path=path,
        observation=VerifierObservation("pytest-0", "artifact", "sha256:fixed", False),
    )


def _timed(run, **kwargs):
    start = time.monotonic()
    got = failure_evidence.capture([run], home="", **kwargs)[0]
    return got, time.monotonic() - start


def test_a_long_id_is_not_masked_when_ids_are_hidden(tmp_path):
    node = "tests/test_a.py::test_a[" + "_" * 50_000 + "=v]"
    got, took = _timed(_run(tmp_path / "log", {node: "failed"}), hidden=True, account=True)
    assert took < LIMIT_SECONDS
    assert got["failed_nodeids"] == [] and got["failed_nodeids_truncated"] is False
    assert got["failed_count"] == 1 and node[:40] not in str(got)


def test_a_long_id_and_many_ids_stay_bounded_when_shown(tmp_path):
    outcomes = {f"tests/test_{i:05}.py::test_a[{'_' * 50_000}=v]": "failed" for i in range(2000)}
    got, took = _timed(_run(tmp_path / "log", outcomes), account=True)
    assert took < LIMIT_SECONDS
    assert len(got["failed_nodeids"]) == failure_evidence.MAX_NODEIDS
    assert all(len(shown) <= failure_evidence.MAX_ID for shown in got["failed_nodeids"])
    assert got["failed_nodeids_truncated"] is True and got["failed_count"] == 2000


def test_a_long_log_line_is_read_in_bounded_time(tmp_path):
    path = tmp_path / "verifier.log"
    path.write_text("FAILED\n" + "://" * 20_000)
    got, took = _timed(_run(path, {"n": "failed"}), account=True)
    assert took < LIMIT_SECONDS
    assert got["log_tail_unavailable"] is False


def test_a_secret_split_by_a_control_character_in_a_log_line_is_still_masked(tmp_path):
    path = tmp_path / "verifier.log"
    path.write_text("ok\nsee https://u:p\rxxx@h/ now\n", newline="")
    got = failure_evidence.capture([_run(path, {"n": "failed"})], home="", account=True)[0]
    assert "u:p" not in got["log_tail"] and "ok" in got["log_tail"]
