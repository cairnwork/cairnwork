"""Regression cases for casebook origin binding (Issue #935)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from attest.test_attest_cli import CORE_IDENTITY, fake_core_verify
from test_acceptance_casebook_origin import _add, _book, _notarize

from cairnwork.notary import Notary
from experimental.acceptance_casebook.origin import origin_still_holds

pytest_plugins = ["test_acceptance_casebook_origin"]


def _wrong_identity_core(request: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    status, response = fake_core_verify(request)
    response["core_identity"] = {"name": "different-core", "version": "0"}
    return status, response


def _wrong_identity_failed_core(request: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    status, response = fake_core_verify(request)
    response["verification"] = {
        "status": "FAILED",
        "integrity": "VALID",
        "reason_codes": ["TEST_FAILURE"],
    }
    response["core_identity"] = {"name": "different-core", "version": "0"}
    return status, response


def test_identity_mismatch_is_not_added_as_accepted(
    lg: dict[str, str], repo: Path, capsys: Any
) -> None:
    db = Path(lg["dir"]) / "mismatch.sqlite"
    candidate = _notarize(
        db, repo, "identity-mismatch", _wrong_identity_core, {"src/mismatch_935.py": "x = 1\n"}
    )
    verdict = Notary(db, core_identity=CORE_IDENTITY).verdict(
        "task-1", "identity-mismatch", expected_candidate=candidate
    )
    assert verdict is not None and verdict.verified is False

    mismatched = {**lg, "db": str(db)}
    assert _add(mismatched, candidate, "identity-mismatch", "accepted_bad") == 2
    assert "CASE_ORIGIN_MISMATCH" in capsys.readouterr().out


def test_failed_identity_mismatch_is_not_added_as_refused(
    lg: dict[str, str], repo: Path, capsys: Any
) -> None:
    failed_db = Path(lg["dir"]) / "failed-mismatch.sqlite"
    failed_candidate = _notarize(
        failed_db,
        repo,
        "failed-identity-mismatch",
        _wrong_identity_failed_core,
        {"src/failed_mismatch_935.py": "x = 2\n"},
    )
    failed = {**lg, "db": str(failed_db)}
    assert _add(failed, failed_candidate, "failed-identity-mismatch", "refused_good") == 2
    assert "CASE_ORIGIN_MISMATCH" in capsys.readouterr().out
    assert not _book(failed).exists()


def test_expected_identity_rewrite_changes_origin(lg: dict[str, str], capsys: Any) -> None:
    assert _add(lg, lg["cand"], "a1", "accepted_bad") == 0
    casebook = _book(lg)
    case = json.loads(casebook.read_text().splitlines()[0])
    assert origin_still_holds(Path(lg["db"]), case)
    with sqlite3.connect(lg["db"]) as conn:
        triggers = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'trigger' AND tbl_name = ?",
            ("core_verifications",),
        ).fetchall()
        for (trigger,) in triggers:
            conn.execute(f'DROP TRIGGER "{trigger}"')
        conn.execute(
            "UPDATE core_verifications SET expected_identity = ? "
            "WHERE task_id = ? AND attempt_id = ?",
            ('{"changed":true}', "task-1", "a1"),
        )
    assert not origin_still_holds(Path(lg["db"]), case)


def test_normally_verified_row_is_added(lg: dict[str, str]) -> None:
    assert _add(lg, lg["cand"], "a1", "accepted_bad") == 0


@pytest.mark.parametrize("column", ["candidate_commit", "binding_json"])
def test_column_rewrite_is_not_added_as_accepted(
    lg: dict[str, str], capsys: Any, column: str
) -> None:
    """A row whose columns no longer match its stored request is not accepted (#935)."""
    value = "f" * 40 if column == "candidate_commit" else '{"head": "x"}'
    with sqlite3.connect(lg["db"]) as conn:
        for (trigger,) in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'trigger' AND tbl_name = ?",
            ("core_verifications",),
        ).fetchall():
            conn.execute(f'DROP TRIGGER "{trigger}"')
        conn.execute(
            f"UPDATE core_verifications SET {column} = ? WHERE task_id = ? AND attempt_id = ?",
            (value, "task-1", "a1"),
        )
    capsys.readouterr()
    candidate = value if column == "candidate_commit" else lg["cand"]
    assert _add(lg, candidate, "a1", "accepted_bad") == 2
    assert not _book(lg).exists()


def test_row_copied_to_another_attempt_is_not_added_as_accepted(
    lg: dict[str, str], capsys: Any
) -> None:
    """A whole row copied under another attempt id (a row without a binding too) must not pass:
    its stored request names the original attempt, so the notary's column check refuses it."""
    with sqlite3.connect(lg["db"]) as conn:
        conn.row_factory = sqlite3.Row
        row = dict(
            conn.execute(
                "SELECT * FROM core_verifications WHERE task_id = ? AND attempt_id = ?",
                ("task-1", "a1"),
            ).fetchone()
        )
        row["attempt_id"] = "a9"
        row.pop("seq")  # the autoincrement key: let the table number the copy
        names = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        conn.execute(
            f"INSERT INTO core_verifications ({names}) VALUES ({marks})", tuple(row.values())
        )
    capsys.readouterr()
    assert _add(lg, lg["cand"], "a9", "accepted_bad") == 2
    assert not _book(lg).exists()


def test_row_swapped_between_the_two_reads_is_refused(
    lg: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The binding is judged on a second read; a row that differs from the first read is refused."""
    from dataclasses import replace

    import experimental.acceptance_casebook.origin as origin_mod

    real = origin_mod.read_record
    calls: list[int] = []

    def swapped(db: Path, task: str, attempt: str):
        rec = real(db, task, attempt)
        calls.append(1)
        return rec if len(calls) == 1 else replace(rec, recorded_at="2099-01-01T00:00:00Z")

    monkeypatch.setattr(origin_mod, "read_record", swapped)
    with pytest.raises(origin_mod.OriginError) as info:
        origin_mod.read_origin(Path(lg["db"]), "task-1", "a1")
    assert info.value.code == "CASE_RECORD_MISMATCH"
    assert len(calls) == 2
