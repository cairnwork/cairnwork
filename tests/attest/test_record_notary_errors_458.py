from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from cairnwork.notary import Notary
from cairnwork_attest.cli import main
from cairnwork_attest.record import RecordMalformed, RecordNotFound, verify_record
from tests.attest.test_record import setup_sample_notary


def test_notary_sqlite_error_is_record_not_found(tmp_path: Path, repo: Path, monkeypatch) -> None:
    db_path = tmp_path / "notary.sqlite"
    _, verdict = setup_sample_notary(db_path, repo)

    def unreadable(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(Notary, "verdict", unreadable)
    with pytest.raises(RecordNotFound, match="database is locked") as caught:
        verify_record(db_path, "task-1", "a1", expected_candidate=verdict.candidate_commit)
    assert caught.value.code == "RECORD_NOT_FOUND"


def test_corrupt_notary_database_is_coded_attest_error(tmp_path: Path) -> None:
    db_path = tmp_path / "notary.sqlite"
    db_path.write_bytes(b"not a sqlite database")
    with pytest.raises(RecordNotFound) as caught:
        verify_record(db_path, "task-1", "a1", expected_candidate="a" * 40)
    assert caught.value.code == "RECORD_NOT_FOUND"


def test_missing_notary_product_is_record_malformed(
    tmp_path: Path, repo: Path, monkeypatch
) -> None:
    db_path = tmp_path / "notary.sqlite"
    _, verdict = setup_sample_notary(db_path, repo)

    def missing_product(*args, **kwargs):
        raise ImportError("nexus package missing")

    monkeypatch.setattr(Notary, "verdict", missing_product)
    with pytest.raises(RecordMalformed) as caught:
        verify_record(db_path, "task-1", "a1", expected_candidate=verdict.candidate_commit)
    assert caught.value.code == "RECORD_MALFORMED"


def test_statement_sqlite_failure_prints_code_and_writes_no_file(
    tmp_path: Path, repo: Path, monkeypatch, capsys
) -> None:
    state = tmp_path / "state"
    state.mkdir()
    db_path = state / "notary.sqlite"
    _, verdict = setup_sample_notary(db_path, repo)
    out_path = tmp_path / "statement.json"

    def locked(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(Notary, "verdict", locked)
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
            verdict.candidate_commit,
            "--out",
            str(out_path),
        ]
    )
    assert rc == 2
    assert capsys.readouterr().err.strip() == "RECORD_NOT_FOUND"
    assert not out_path.exists()
