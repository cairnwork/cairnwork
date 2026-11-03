"""The statement refuses when the notary row differs between its two reads."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from test_attest_cli import setup_cli_fixture

from cairnwork_attest import record as record_mod
from cairnwork_attest.cli import main


@pytest.mark.parametrize(
    ("field", "value"),
    [("status", "FAILED_VERIFICATION"), ("integrity", "INVALID"), ("http_status", 500)],
)
def test_statement_refused_when_row_changes_between_reads(
    tmp_path: Path,
    repo: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: Any,
) -> None:
    state = tmp_path / "state"
    candidate = setup_cli_fixture(state, repo)
    out_file = tmp_path / "statement.json"
    real_read = record_mod.read_record

    def first_read_differs(*args: Any, **kwargs: Any) -> Any:
        return replace(real_read(*args, **kwargs), **{field: value})

    monkeypatch.setattr(record_mod, "read_record", first_read_differs)
    rc = main(
        [
            "statement",
            "--state",
            str(state),
            "--task-id",
            "task-1",
            "--attempt-id",
            "a1",
            "--candidate",
            candidate,
            "--out",
            str(out_file),
        ]  # fmt: skip
    )
    assert rc == 2
    assert "RECORD_MALFORMED" in capsys.readouterr().err
    assert not out_file.exists()
