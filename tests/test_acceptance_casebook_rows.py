# ruff: noqa: F811  (the lg fixture is imported, then requested by name)
"""Acceptance casebook #772 step 1, round 2: the bound row must hold a real Core verdict whose
columns agree with its own request; two attempts on one candidate reach CASE_CONFLICT."""

from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from attest.test_attest_cli import CORE_IDENTITY, fake_core_verify
from test_acceptance_casebook_origin import _add, _book, _ev, _list, _no_harness_session, lg  # noqa: F401

from cairnwork.core_request import AcceptanceSpec, VerifierObservation
from cairnwork.notary import Notary


def _core_down(_request: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    return 502, {"error": {"code": "CORE_UNAVAILABLE"}}


def _refusing(request: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    status, response = fake_core_verify(request)
    response["verification"] = {"status": "FAILED", "integrity": "VALID", "reason_codes": ["X"]}
    return status, response


def _attempt(lg: dict[str, str], repo: Path, attempt: str, core: Any, cand: str) -> None:  # noqa: F811
    """Notarize another attempt on an existing candidate commit."""
    notary = Notary(Path(lg["db"]), core_verify=core, core_identity=CORE_IDENTITY)
    spec = AcceptanceSpec("task-1", "sha256:" + "c" * 64, ("pytest",), ("src/a.py",), False)
    obs = (VerifierObservation("pytest", "artifact-1", "sha256:" + "e" * 64, True),)
    notary.notarize("task-1", attempt, spec, repo, lg["base"], cand, obs)


def test_error_row_is_not_an_original_refusal(lg, repo, capsys) -> None:  # noqa: F811
    _attempt(lg, repo, "a5", _core_down, lg["cand"])
    capsys.readouterr()
    assert _add(lg, lg["cand"], "a5", "refused_good") == 2
    assert "CASE_ORIGIN_MISMATCH" in capsys.readouterr().out
    assert _add(lg, lg["cand"], "a5", "accepted_bad") == 2
    assert not _book(lg).exists()


def _tamper(
    lg: dict[str, str], tmp_path: Path, column: str, value: str, attempt: str = "a1"
) -> str:  # noqa: F811
    copy = tmp_path / "tampered.sqlite"
    shutil.copy(lg["db"], copy)
    conn = sqlite3.connect(copy)
    for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'"):
        conn.execute(f"DROP TRIGGER {name}")
    conn.execute(
        f"UPDATE core_verifications SET {column} = ? WHERE attempt_id = ?", (value, attempt)
    )
    conn.commit()
    conn.close()
    return str(copy)


@pytest.mark.parametrize("column", ["request_hash", "candidate_commit"])
def test_row_whose_columns_disagree_with_its_request_refused(lg, tmp_path, capsys, column) -> None:  # noqa: F811
    value = "sha256:" + "f" * 64 if column == "request_hash" else "1" * 40
    db = _tamper(lg, tmp_path, column, value)
    cand = value if column == "candidate_commit" else lg["cand"]
    capsys.readouterr()
    assert _add({**lg, "db": db}, cand, "a1", "accepted_bad") == 2
    assert "CASE_RECORD_MISMATCH" in capsys.readouterr().out
    assert not _book(lg).exists()


def test_two_attempts_with_different_verdicts_conflict(lg, repo, capsys) -> None:  # noqa: F811
    _attempt(lg, repo, "a3", _refusing, lg["cand"])
    assert _add(lg, lg["cand"], "a1", "accepted_bad") == 0
    capsys.readouterr()
    assert _add(lg, lg["cand"], "a3", "refused_good") == 2
    assert "CASE_CONFLICT" in capsys.readouterr().out
    assert len(_book(lg).read_text().splitlines()) == 1


def test_same_kind_other_attempt_is_its_own_line(lg, repo) -> None:  # noqa: F811
    _attempt(lg, repo, "a4", fake_core_verify, lg["cand"])
    assert _add(lg, lg["cand"], "a1", "accepted_bad") == 0
    assert _add(lg, lg["cand"], "a4", "accepted_bad") == 0
    assert _add(lg, lg["cand"], "a4", "accepted_bad") == 0  # DUPLICATE now
    assert [json.loads(x)["attempt_id"] for x in _book(lg).read_text().splitlines()] == [
        "a1", "a4"]  # fmt: skip


def test_rewritten_evidence_file_is_origin_changed(lg, capsys) -> None:  # noqa: F811
    assert _add(lg, lg["cand"], "a1", "accepted_bad") == 0
    evidence = Path(_list(lg, capsys)[0]["after"]["file"])
    evidence.write_text(evidence.read_text() + " ")
    assert _list(lg, capsys, "--notary-db", lg["db"])[0]["state"] == "origin_changed"


def test_confirmation_without_confirmer_does_not_count(lg, capsys) -> None:  # noqa: F811
    from experimental.acceptance_casebook.store import _append, _read_chain

    assert _add(lg, lg["cand"], "a1", "accepted_bad") == 0
    digest = _list(lg, capsys)[0]["line_sha256"]
    line = {"type": "confirmation", "case": digest, "conclusion": "confirmed"}
    _append(_book(lg), line, _read_chain(_book(lg))[1])
    assert _list(lg, capsys)[0]["state"] == "unconfirmed"


def _verified_without_echo(request: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    status, response = fake_core_verify(request)
    response["hashes"] = {}  # VERIFIED/VALID/200, but Core did not echo what it judged
    return status, response


def test_verified_row_that_fails_the_notary_rule_is_not_accepted(lg, repo, capsys) -> None:  # noqa: F811
    _attempt(lg, repo, "a6", _verified_without_echo, lg["cand"])
    capsys.readouterr()
    assert _add(lg, lg["cand"], "a6", "accepted_bad") == 2
    assert "CASE_ORIGIN_MISMATCH" in capsys.readouterr().out
    assert not _book(lg).exists()


def _core_says(status: str, integrity: str = "VALID", http: int = 200) -> Any:
    def core(request: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        _, response = fake_core_verify(request)
        response["verification"] = {"status": status, "integrity": integrity, "reason_codes": []}
        return http, response

    return core


@pytest.mark.parametrize(
    ("status", "integrity", "http"),
    [("UNCOMPARABLE", "VALID", 200), ("INCOMPLETE", "VALID", 200), ("FAILED", "INVALID", 200),
     ("FAILED", "VALID", 500)],
)  # fmt: skip
def test_only_a_core_refusal_is_an_original_refusal(lg, repo, capsys, status, integrity, http):  # noqa: F811
    _attempt(lg, repo, "a7", _core_says(status, integrity, http), lg["cand"])
    capsys.readouterr()
    assert _add(lg, lg["cand"], "a7", "refused_good") == 2
    assert "CASE_ORIGIN_MISMATCH" in capsys.readouterr().out
    assert not _book(lg).exists()


@pytest.mark.parametrize(
    ("attempt", "column_says", "kind"),
    [("a1", "FAILED", "refused_good"), ("a3", "VERIFIED", "accepted_bad")],
)
def test_status_column_that_disagrees_with_response_refused(
    lg,
    repo,
    tmp_path,
    capsys,
    attempt,
    column_says,
    kind,  # noqa: F811
) -> None:
    _attempt(lg, repo, "a3", _refusing, lg["cand"])
    db = _tamper(lg, tmp_path, "status", column_says, attempt)
    capsys.readouterr()
    assert _add({**lg, "db": db}, lg["cand"], attempt, kind) == 2
    assert "CASE_RECORD_MISMATCH" in capsys.readouterr().out
    assert not _book(lg).exists()
