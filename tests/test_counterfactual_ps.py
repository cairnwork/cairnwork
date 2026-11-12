from __future__ import annotations

import json
from pathlib import Path

import pytest

from experimental.counterfactual_ps import ERROR, FAILED, NOT_APPLICABLE, UNCOMPARABLE, cli, compare

SANDBOX = (
    "FAILED tests/test_a.py::test_x - PermissionError: [Errno 1] Operation not permitted: 'ps'\n"
    "=== 1 failed, 3 passed in 1.00s ===\n"
)
CLEAN = "....\n=== 4 passed in 0.50s ===\n"


def test_clean_outside_run_with_ps_denial_is_uncomparable() -> None:
    assert compare(SANDBOX, CLEAN).verdict == UNCOMPARABLE


def test_wrong_candidate_with_real_failures_stays_failed() -> None:
    plain = "FAILED tests/test_a.py::test_y - assert 1 == 2\n=== 1 failed, 3 passed in 0.5s ===\n"
    assert compare(SANDBOX, plain).verdict == FAILED


def test_failed_count_without_unit_lines_stays_failed() -> None:
    assert compare(SANDBOX, "=== 2 failed, 2 passed in 0.5s ===\n").verdict == FAILED


def test_error_count_outside_stays_failed() -> None:
    assert compare(SANDBOX, "=== 3 passed, 1 error in 0.5s ===\n").verdict == FAILED


def test_skips_outside_stay_failed() -> None:
    assert compare(SANDBOX, "=== 3 passed, 1 skipped in 0.5s ===\n").verdict == FAILED


def test_no_pytest_summary_is_error_not_pass() -> None:
    assert compare(SANDBOX, "No module named pytest\n").verdict == ERROR
    assert compare(SANDBOX, "").verdict == ERROR


def test_guard_tripped_is_error() -> None:
    assert compare(SANDBOX, CLEAN, guard_tripped=True).verdict == ERROR


def test_no_ps_signature_stays_failed() -> None:
    log = "FAILED tests/test_a.py::test_x - assert False\n=== 1 failed in 1s ===\n"
    assert compare(log, CLEAN).verdict == FAILED


def test_ps_string_planted_in_text_does_not_hide_real_failure_outside() -> None:
    plain = "FAILED tests/test_a.py::test_x - nope\n=== 1 failed, 3 passed in 1s ===\n"
    assert compare(SANDBOX + "echo 'ps: Operation not permitted'", plain).verdict == FAILED


def test_no_sandbox_failures_is_not_applicable() -> None:
    assert compare("=== 4 passed in 1s ===\n", CLEAN).verdict == NOT_APPLICABLE


def test_verdict_is_never_verified() -> None:
    for sb in (SANDBOX, "", "FAILED a::b\n"):
        for pl in (CLEAN, "", "=== 1 failed in 1s ===\n"):
            assert compare(sb, pl).verdict in {FAILED, UNCOMPARABLE, ERROR, NOT_APPLICABLE}


def test_cli_off_by_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv(cli.ENV, raising=False)
    assert cli.main([str(tmp_path / "a"), str(tmp_path / "b")]) == 2


def test_cli_reads_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(cli.ENV, "1")
    (tmp_path / "s").write_text(SANDBOX)
    (tmp_path / "p").write_text(CLEAN)
    assert cli.main([str(tmp_path / "s"), str(tmp_path / "p")]) == 0
    assert json.loads(capsys.readouterr().out)["verdict"] == UNCOMPARABLE
