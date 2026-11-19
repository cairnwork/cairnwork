"""#935: Notary.row_binding answers the identity and column questions for a row of any status."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from attest.test_attest_cli import CORE_IDENTITY, fake_core_verify
from conftest import commit_change, git

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
    assert got.expected_identity is not None


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
