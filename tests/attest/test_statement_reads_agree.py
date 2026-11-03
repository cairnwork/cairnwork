"""The statement refuses when the notary row differs between its two reads (#750)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from cairnwork_attest import record as record_mod
from cairnwork_attest.cli import main
from cairnwork_attest.record import RecordMalformed, verify_record
from tests.attest.test_attest_cli import setup_cli_fixture

CHANGES = [
    ("status", "FAILED_VERIFICATION"), ("integrity", "INVALID"), ("http_status", 500),
    ("request_hash", "sha256:" + "0" * 64), ("base_commit", "1" * 40),
    ("response", {"status": "VERIFIED", "changed": True}),
]  # fmt: skip


def _first_read(monkeypatch: pytest.MonkeyPatch, field: str, value: Any) -> None:
    real_read = record_mod.read_record
    # the first read stays self-consistent, so only the comparison of the two reads can refuse
    monkeypatch.setattr(record_mod, "check_record_bindings", lambda _r: None)
    monkeypatch.setattr(
        record_mod, "read_record", lambda *a, **k: replace(real_read(*a, **k), **{field: value})
    )


@pytest.mark.parametrize(("field", "value"), CHANGES)
def test_a_row_that_changes_between_reads_is_refused_and_named(
    tmp_path: Path, repo: Path, monkeypatch: pytest.MonkeyPatch, field: str, value: Any
) -> None:
    candidate = setup_cli_fixture(tmp_path / "state", repo)
    _first_read(monkeypatch, field, value)
    with pytest.raises(RecordMalformed, match=f"differs between the two reads: {field}$"):
        verify_record(tmp_path / "state" / "notary.sqlite", "task-1", "a1",
                      expected_candidate=candidate)  # fmt: skip


def test_the_statement_cli_writes_nothing(
    tmp_path: Path, repo: Path, capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    candidate = setup_cli_fixture(tmp_path / "state", repo)
    _first_read(monkeypatch, "status", "FAILED_VERIFICATION")
    out = tmp_path / "statement.json"
    rc = main(["statement", "--state", str(tmp_path / "state"), "--task-id", "task-1",
               "--attempt-id", "a1", "--candidate", candidate, "--out", str(out)])  # fmt: skip
    assert rc == 2 and "RECORD_MALFORMED" in capsys.readouterr().err and not out.exists()


def test_a_difference_is_named_even_when_the_second_read_is_not_verified(
    tmp_path: Path, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = setup_cli_fixture(tmp_path / "state", repo)
    real_verdict = record_mod.Notary.verdict

    def second_read_changed(self: Any, *args: Any, **kwargs: Any) -> Any:
        verdict = real_verdict(self, *args, **kwargs)
        assert verdict is not None
        return replace(verdict, verified=False, status="FAILED")

    monkeypatch.setattr(record_mod.Notary, "verdict", second_read_changed)
    with pytest.raises(RecordMalformed, match="differs between the two reads: status$"):
        verify_record(tmp_path / "state" / "notary.sqlite", "task-1", "a1",
                      expected_candidate=candidate)  # fmt: skip
