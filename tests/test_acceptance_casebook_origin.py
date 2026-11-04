"""Acceptance casebook #772 P0-A (original evidence bound, kept apart from later evidence) and
P0-C (only confirmed cases whose origin re-checks are eligible)."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from attest.test_attest_cli import CORE_IDENTITY, fake_core_verify, setup_cli_fixture
from conftest import commit_change, git

from cairnwork.core_request import AcceptanceSpec, VerifierObservation
from cairnwork.notary import Notary
from experimental.acceptance_casebook.cli import main


def _failing_core(request: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    status, response = fake_core_verify(request)
    response["verification"] = {"status": "FAILED", "integrity": "VALID", "reason_codes": ["X"]}
    return status, response


def _notarize(db: Path, repo: Path, attempt: str, core: Any, files: dict[str, str]) -> str:
    notary = Notary(db, core_verify=core, core_identity=CORE_IDENTITY)
    base = git(repo, "rev-parse", "HEAD")
    cand = commit_change(repo, files)
    spec = AcceptanceSpec("task-1", "sha256:" + "c" * 64, ("pytest",), tuple(files), False)
    obs = (VerifierObservation("pytest", "artifact-1", "sha256:" + "e" * 64, True),)
    notary.notarize("task-1", attempt, spec, repo, base, cand, obs)
    git(repo, "reset", "-q", "--hard", base)
    return cand


@pytest.fixture(autouse=True)
def _no_harness_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)


@pytest.fixture
def lg(tmp_path: Path, repo: Path) -> dict[str, str]:
    """Row a1: accepted (VERIFIED). Row a2: refused (FAILED), another candidate."""
    base = git(repo, "rev-parse", "HEAD")
    state = tmp_path / "state"
    cand = setup_cli_fixture(state, repo)
    git(repo, "reset", "-q", "--hard", base)
    db = state / "notary.sqlite"
    refused = _notarize(db, repo, "a2", _failing_core, {"src/refused_772.py": "y = 1\n"})
    return {"db": str(db), "base": base, "cand": cand, "refused": refused, "dir": str(tmp_path)}


def _ev(lg: dict[str, str], name: str, **data: str) -> str:
    path = Path(lg["dir"]) / f"{name}.json"
    path.write_text(json.dumps({"task_id": "task-1", "ref": "issue-1", **data}))
    return str(path)


def _add(lg: dict[str, str], cand: str, attempt: str, kind: str, version: str = "v1",
         ev: dict[str, str] | None = None) -> int:  # fmt: skip
    claim = {"candidate": cand, "attempt_id": attempt, "kind": kind, **(ev or {})}
    return main(["add", "--casebook", str(_book(lg)), "--notary-db", lg["db"],
                 "--task-id", "task-1", "--attempt-id", attempt, "--candidate", cand,
                 "--base", lg["base"], "--version", version, "--kind", kind,
                 "--found-by", "reviewer-x", "--dispatch-id", "adder-1", "--lineage", "root",
                 "--model-family", "anthropic", "--source-file", _ev(lg, "adder-src"),
                 "--evidence-file", _ev(lg, f"ev-{attempt}-{version}", **claim)])  # fmt: skip


def _book(lg: dict[str, str]) -> Path:
    return Path(lg["dir"]) / "cb.jsonl"


def _list(lg: dict[str, str], capsys: pytest.CaptureFixture[str], *extra: str) -> list[dict]:
    capsys.readouterr()
    assert main(["list", "--casebook", str(_book(lg)), "--json", *extra]) == 0
    return json.loads(capsys.readouterr().out)["cases"]


def test_accepted_row_case_keeps_origin_and_after_apart(
    lg: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert _add(lg, lg["cand"], "a1", "accepted_bad") == 0
    (case,) = _list(lg, capsys)
    assert case["type"] == "case" and case["state"] == "unconfirmed"
    assert case["origin"]["status"] == "VERIFIED" and case["origin"]["accepted"] is True
    assert len(case["origin"]["row_sha256"]) == 64 and case["origin"]["request_hash"]
    assert case["after"]["ref"] == "issue-1" and len(case["after"]["sha256"]) == 64
    assert "evidence" not in case and "evidence_file" not in case


@pytest.mark.parametrize(
    ("attempt", "kind"), [("a2", "accepted_bad"), ("a1", "refused_good")]
)  # P0-A 1 and 2
def test_kind_must_match_original_verdict(
    lg: dict[str, str], capsys: pytest.CaptureFixture[str], attempt: str, kind: str
) -> None:
    cand = lg["refused"] if attempt == "a2" else lg["cand"]
    capsys.readouterr()
    assert _add(lg, cand, attempt, kind) == 2
    assert "CASE_ORIGIN_MISMATCH" in capsys.readouterr().out
    assert not _book(lg).exists()


def test_refused_row_refused_good_is_added(lg: dict[str, str]) -> None:
    assert _add(lg, lg["refused"], "a2", "refused_good") == 0


@pytest.mark.parametrize("wrong", ["candidate", "kind", "attempt_id"])  # P0-A 5
def test_evidence_that_names_another_claim_refused(
    lg: dict[str, str], capsys: pytest.CaptureFixture[str], wrong: str
) -> None:
    other = {"candidate": "1" * 40, "kind": "refused_good", "attempt_id": "a2"}[wrong]
    capsys.readouterr()
    assert _add(lg, lg["cand"], "a1", "accepted_bad", ev={wrong: other}) == 2
    assert "CASE_EVIDENCE_MISMATCH" in capsys.readouterr().out
    assert not _book(lg).exists()


@pytest.mark.parametrize("field", ["request_hash", "binding", "core_identity"])  # P0-A 3
def test_origin_changes_with_request_binding_or_core_identity(lg: dict[str, str], field: str):
    from experimental.acceptance_casebook.origin import origin_of, read_origin

    rec, origin = read_origin(Path(lg["db"]), "task-1", "a1")
    if field == "request_hash":
        changed = replace(rec, request_hash="sha256:" + "f" * 64)
    elif field == "binding":
        changed = replace(rec, binding_json='{"head": "x"}', binding=None)
    else:
        resp = {**rec.response, "core_identity": {"other": True}}
        changed = replace(rec, response_json=json.dumps(resp), response=resp)
    assert origin_of(changed) != origin


def test_replaced_ledger_same_ids_is_origin_changed(
    lg: dict[str, str], repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:  # P0-A 4
    assert _add(lg, lg["cand"], "a1", "accepted_bad") == 0
    other = tmp_path / "other" / "notary.sqlite"
    other.parent.mkdir()
    git(repo, "reset", "-q", "--hard", lg["cand"] + "~1")
    _notarize(
        other, repo, "a1", fake_core_verify, {"src/a.py": "x = 2\n", "src/other_772.py": "z = 0\n"}
    )
    (case,) = _list(lg, capsys, "--notary-db", str(other))
    assert case["state"] == "origin_changed"
    assert _list(lg, capsys, "--notary-db", lg["db"])[0]["state"] == "unconfirmed"


def _confirm(lg: dict[str, str], digest: str, conclusion: str = "confirmed") -> None:
    from experimental.acceptance_casebook.confirm import check_record, confirm_case
    from experimental.acceptance_casebook.identity import resolve_actor

    opts = {"dispatch_id": "conf-1", "lineage": "root", "model_family": "google",
            "source_file": _ev(lg, "conf-src")}  # fmt: skip
    who = resolve_actor({}, opts)
    check = check_record("rerun", _ev(lg, "check"))
    confirm_case(_book(lg), digest, who, conclusion, check)


def test_only_confirmed_cases_with_intact_origin_are_eligible(
    lg: dict[str, str], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:  # P0-C
    for v in ("v1", "v2", "v3", "v4"):
        assert _add(lg, lg["cand"], "a1", "accepted_bad", version=v) == 0
    cases = _list(lg, capsys)
    _confirm(lg, cases[0]["line_sha256"])
    _confirm(lg, cases[2]["line_sha256"])
    assert [c["state"] for c in _list(lg, capsys)] == [
        "confirmed", "unconfirmed", "confirmed", "unconfirmed"]  # fmt: skip
    assert all(c["type"] == "case" for c in _list(lg, capsys))  # confirmation is not a case
    eligible = _list(lg, capsys, "--notary-db", lg["db"], "--eligible")
    assert [c["version"] for c in eligible] == ["v1", "v3"]
    gone = tmp_path / "missing.sqlite"
    assert _list(lg, capsys, "--notary-db", str(gone), "--eligible") == []


def test_eligible_needs_notary_db(lg: dict[str, str], capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["list", "--casebook", str(_book(lg)), "--eligible"]) == 2
    assert "CASE_INVALID" in capsys.readouterr().out


def test_rejected_confirmation_is_not_eligible(
    lg: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert _add(lg, lg["cand"], "a1", "accepted_bad") == 0
    digest = _list(lg, capsys)[0]["line_sha256"]
    _confirm(lg, digest)
    _confirm(lg, digest, "rejected")
    assert _list(lg, capsys, "--notary-db", lg["db"], "--eligible") == []
