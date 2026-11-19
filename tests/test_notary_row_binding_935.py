"""#935: Notary.row_binding answers the identity and column questions for a row of any status."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from attest.test_attest_cli import CORE_IDENTITY, fake_core_verify
from conftest import commit_change, git

from cairnwork import notary_binding
from cairnwork.core_request import AcceptanceSpec, VerifierObservation
from cairnwork.notary import Notary


def _failing(request: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    status, response = fake_core_verify(request)
    response["verification"] = {"status": "FAILED", "integrity": "VALID", "reason_codes": ["X"]}
    return status, response


def _row(tmp_path: Path, repo: Path, core: Any) -> tuple[Notary, Path]:
    db = tmp_path / "notary.db"
    notary = Notary(db, core_verify=core, core_identity=CORE_IDENTITY)
    base = git(repo, "rev-parse", "HEAD")
    cand = commit_change(repo, {"src/b935.py": "x = 1\n"})
    spec = AcceptanceSpec("task-1", "sha256:" + "c" * 64, ("pytest-0",), ("src/b935.py",), False)
    obs = (VerifierObservation("pytest-0", "artifact-1", "sha256:" + "e" * 64, True),)
    notary.notarize("task-1", "a1", spec, repo, base, cand, obs)
    return notary, db


def _sql(db: Path, sql: str, *params: object) -> None:
    conn = sqlite3.connect(db)
    try:
        conn.execute("DROP TRIGGER IF EXISTS core_verifications_no_update")
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def _rewrite_identity(db: Path, identity: dict[str, Any]) -> None:
    conn = sqlite3.connect(db)
    try:
        conn.execute("DROP TRIGGER IF EXISTS core_verifications_no_update")
        (raw,) = conn.execute("SELECT response_json FROM core_verifications").fetchone()
        resp = {**json.loads(raw), "core_identity": identity}
        conn.execute("UPDATE core_verifications SET response_json = ?", (json.dumps(resp),))
        conn.commit()
    finally:
        conn.close()


def test_verified_row_is_bound(tmp_path: Path, repo: Path) -> None:
    notary, _ = _row(tmp_path, repo, fake_core_verify)
    got = notary.row_binding("task-1", "a1")
    assert got is not None
    assert got.identity_problem == "" and got.columns_match is True
    assert (
        got.expected_identity_sha256
        == hashlib.sha256(
            json.dumps(CORE_IDENTITY, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )


def test_identity_rewritten_after_the_fact_is_reported(tmp_path: Path, repo: Path) -> None:
    notary, db = _row(tmp_path, repo, fake_core_verify)
    _rewrite_identity(db, {"other": True})
    got = notary.row_binding("task-1", "a1")
    assert got is not None and got.identity_problem != ""


def test_failed_row_with_foreign_identity_still_reports_the_problem(
    tmp_path: Path, repo: Path
) -> None:
    notary, db = _row(tmp_path, repo, _failing)
    assert notary.row_binding("task-1", "a1").identity_problem == ""  # type: ignore[union-attr]
    _rewrite_identity(db, {"other": True})
    got = notary.row_binding("task-1", "a1")
    assert got is not None and got.identity_problem != ""


def test_changed_candidate_column_breaks_the_column_match(tmp_path: Path, repo: Path) -> None:
    notary, db = _row(tmp_path, repo, fake_core_verify)
    _sql(db, "UPDATE core_verifications SET candidate_commit = ?", "f" * 40)
    got = notary.row_binding("task-1", "a1")
    assert got is not None and got.columns_match is False


def test_missing_row_is_none(tmp_path: Path, repo: Path) -> None:
    notary, _ = _row(tmp_path, repo, fake_core_verify)
    assert notary.row_binding("task-1", "nope") is None
    assert notary.row_binding("other-task", "a1") is None


def test_unreadable_response_is_never_bound(tmp_path: Path, repo: Path) -> None:
    notary, db = _row(tmp_path, repo, fake_core_verify)
    _sql(db, "UPDATE core_verifications SET response_json = ?", "[1, 2")
    got = notary.row_binding("task-1", "a1")
    assert got is not None and got.identity_problem != "" and got.columns_match is False


@pytest.mark.parametrize("column", ["request_json", "response_json"])
def test_null_json_is_unreadable(tmp_path: Path, repo: Path, column: str) -> None:
    notary, db = _row(tmp_path, repo, fake_core_verify)
    # Replace the constrained production table with an unconstrained readback fixture so NULL can
    # model corrupt/legacy storage despite the current schema's NOT NULL constraint.
    _unconstrained_row(db, column, None)
    got = notary.row_binding("task-1", "a1")
    assert got is not None
    assert got.identity_problem == notary_binding.IDENTITY_UNREADABLE
    assert got.columns_match is False


@pytest.mark.parametrize("column", ["request_json", "response_json"])
def test_invalid_json_is_unreadable(tmp_path: Path, repo: Path, column: str) -> None:
    notary, db = _row(tmp_path, repo, fake_core_verify)
    _sql(db, f"UPDATE core_verifications SET {column} = ?", "{")
    got = notary.row_binding("task-1", "a1")
    assert got is not None and got.identity_problem == notary_binding.IDENTITY_UNREADABLE
    assert got.columns_match is False


def test_too_deep_json_is_unreadable(tmp_path: Path, repo: Path) -> None:
    notary, db = _row(tmp_path, repo, fake_core_verify)
    _sql(db, "UPDATE core_verifications SET response_json = ?", "[" * 2000 + "0" + "]" * 2000)
    got = notary.row_binding("task-1", "a1")
    assert got is not None and got.identity_problem == notary_binding.IDENTITY_UNREADABLE
    assert got.columns_match is False


@pytest.mark.parametrize("column", ["request_json", "response_json"])
def test_wrong_sqlite_json_type_is_unreadable(tmp_path: Path, repo: Path, column: str) -> None:
    notary, db = _row(tmp_path, repo, fake_core_verify)
    _unconstrained_row(db, column, 7)
    got = notary.row_binding("task-1", "a1")
    assert got is not None and got.identity_problem == notary_binding.IDENTITY_UNREADABLE
    assert got.columns_match is False


@pytest.mark.parametrize("method", ["_identity_problem", "_columns_match"])
def test_judgement_errors_are_unreadable(
    tmp_path: Path, repo: Path, monkeypatch: pytest.MonkeyPatch, method: str
) -> None:
    notary, _ = _row(tmp_path, repo, fake_core_verify)

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise ValueError("bad stored value")

    if method == "_identity_problem":
        monkeypatch.setattr(notary, method, broken)
    else:
        monkeypatch.setattr(notary_binding, method, broken)
    got = notary.row_binding("task-1", "a1")
    assert got is not None and got.identity_problem == notary_binding.IDENTITY_UNREADABLE
    assert got.columns_match is False


def test_database_read_failure_raises_not_absence(
    tmp_path: Path, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    notary, _ = _row(tmp_path, repo, fake_core_verify)

    def failed_connect(*args: Any, **kwargs: Any) -> Any:
        raise sqlite3.OperationalError("database unavailable")

    monkeypatch.setattr(notary_binding.sqlite3, "connect", failed_connect)
    with pytest.raises(notary_binding.RowBindingUnavailable):
        notary.row_binding("task-1", "a1")


def test_database_query_failure_closes_connection(
    tmp_path: Path, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    notary, _ = _row(tmp_path, repo, fake_core_verify)

    class FailedConnection:
        closed = False

        def execute(self, *_args: Any, **_kwargs: Any) -> Any:
            raise sqlite3.DatabaseError("corrupt database")

        def close(self) -> None:
            self.closed = True

    conn = FailedConnection()
    monkeypatch.setattr(notary_binding.sqlite3, "connect", lambda *_args, **_kwargs: conn)
    with pytest.raises(notary_binding.RowBindingUnavailable):
        notary.row_binding("task-1", "a1")
    assert conn.closed is True


def test_absent_and_unreadable_are_distinct(tmp_path: Path, repo: Path) -> None:
    notary, db = _row(tmp_path, repo, fake_core_verify)
    assert notary.row_binding("task-1", "missing") is None
    _sql(db, "UPDATE core_verifications SET response_json = ?", "not-json")
    got = notary.row_binding("task-1", "a1")
    assert got is not None and got.identity_problem == notary_binding.IDENTITY_UNREADABLE


def _unconstrained_row(db: Path, column: str, value: Any) -> None:
    """Allow malformed values that the current schema correctly rejects on write."""
    assert column in {"request_json", "response_json"}
    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            "SELECT task_id, attempt_id, base_commit, candidate_commit, request_json,"
            " response_json, expected_identity, binding_json FROM core_verifications"
        ).fetchone()
        conn.execute("DROP TABLE core_verifications")
        conn.execute(
            "CREATE TABLE core_verifications (task_id, attempt_id, base_commit,"
            " candidate_commit, request_json, response_json, expected_identity, binding_json)"
        )
        values = list(row)
        values[4 if column == "request_json" else 5] = value
        conn.execute("INSERT INTO core_verifications VALUES (?, ?, ?, ?, ?, ?, ?, ?)", values)
        conn.commit()
    finally:
        conn.close()
