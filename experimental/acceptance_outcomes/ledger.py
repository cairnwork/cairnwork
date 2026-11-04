"""Record acceptance calls and append follow-up events (AC1-AC4).

A call line is written once and never changed; everything later is a separate event line. A
``finding`` is only a suspicion. It changes the outcome only when a ``confirmation`` by an
identity independent of the finder is appended. Independence here is a placeholder: the confirmer
id must differ from the finder id and must not be a declared clone of it (same rule shape as the
casebook). #772 will replace identity sourcing.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from cairnwork_attest.record import AttestError, read_record
from experimental.acceptance_outcomes.store import OutcomesError, append_checked

VERDICTS = ("VERIFIED", "FAILED", "REFUSED", "UNCOMPARABLE")
PRE_CORE = "PRE_CORE"
SOURCES = ("deferred_test", "p2p", "code_review", "human_use", "later_pr", "user_report")
VERIFIED_KINDS = ("not_fixed", "regression", "environment", "requirement_changed")
OTHER_KINDS = ("refused_but_correct",)
EVENT_TYPES = ("observed", "finding", "confirmation", "closed")


def _verdict_of(result: dict[str, Any]) -> str:
    status = result.get("status")
    if status in VERDICTS:
        return str(status)
    if result.get("error") or result.get("code") or result.get("refused"):
        return PRE_CORE  # check_task / intake refusal: no TaskResult exists
    raise OutcomesError("OUTCOME_INVALID", "result has no status and no error code")


def _reached_core(verdict: str, result: dict[str, Any]) -> bool:
    """FAILED and VERIFIED always went through Core. REFUSED and UNCOMPARABLE are set before Core
    is asked (verify_candidate), so they count only when the call's own ``core_status`` (the notary
    verdict it printed) is non-empty. A pre-Core error has no TaskResult at all."""
    if verdict in ("VERIFIED", "FAILED"):
        return True
    return verdict in VERDICTS and bool(result.get("core_status"))


def _notary_state(
    verdict: str,
    call: dict[str, Any],
    result: dict[str, Any],
    db: Path | None,
    lost: str | None = None,
):
    """Return "row", "no_notary_row" (pre-Core only) or "notary_lost"; OUTCOME_RECORD_MISMATCH when
    the notary disagrees: a row with another candidate, base or status, a Core result without its
    row (AC1c), or any row at all behind a call labelled as refused before Core."""
    reached = _reached_core(verdict, result)
    if lost is not None:
        if not reached or db is not None:
            raise OutcomesError("OUTCOME_INVALID", "notary-lost needs a Core result and no db")
        return "notary_lost"
    task, attempt = result.get("task_id"), result.get("attempt_id")
    if not task or not attempt or db is None:
        if reached:
            raise OutcomesError("OUTCOME_RECORD_MISMATCH", f"{verdict} reached Core: no notary row")
        return "no_notary_row"
    try:
        rec = read_record(db, str(task), str(attempt))
    except AttestError as exc:
        if not reached and exc.code == "RECORD_NOT_FOUND":
            return "no_notary_row"
        raise OutcomesError("OUTCOME_RECORD_MISMATCH", f"notary row: {exc.code}") from exc
    if rec.candidate_commit != call["candidate"] or rec.base_commit != call["base"]:
        raise OutcomesError("OUTCOME_RECORD_MISMATCH", "candidate or base differs from notary row")
    if rec.status != verdict:
        raise OutcomesError("OUTCOME_RECORD_MISMATCH", f"verdict {verdict} but row {rec.status}")
    return "row"


def record_call(
    path: Path,
    result: dict[str, Any],
    inputs: dict[str, str],
    notary_db: Path | None = None,
    notary_lost: str | None = None,
) -> str:
    """Append one call line. ``inputs``: repo, base, candidate, version, optional test_commit.
    ``notary_lost`` (a reason) marks a Core result whose notary database is gone (AC1d)."""
    if not isinstance(result, dict):
        raise OutcomesError("OUTCOME_INVALID", "result must be a JSON object")
    call: dict[str, Any] = {k: inputs.get(k, "") for k in ("repo", "base", "candidate", "version")}
    if any(not call[k] for k in call):
        raise OutcomesError("OUTCOME_INVALID", "repo, base, candidate and version are required")
    call["test_commit"] = inputs.get("test_commit", "")
    verdict = _verdict_of(result)
    call.update(verdict=verdict, reason=str(result.get("reason") or result.get("error") or ""))
    call["task_id"], call["attempt_id"] = result.get("task_id", ""), result.get("attempt_id", "")
    lost = None if notary_lost is None else notary_lost.strip()
    if lost == "":  # given but empty or blank: never read as "flag not passed"
        raise OutcomesError("OUTCOME_INVALID", "notary-lost needs a reason")
    state = _notary_state(verdict, call, result, notary_db, lost)
    call["no_notary_row"] = state == "no_notary_row"
    if state == "notary_lost":  # keys exist only on such lines: old lines keep their format (AC1e)
        call["notary_lost"], call["notary_lost_reason"] = True, lost
    # The key holds every recorded field including the reason, so the same inputs refused for two
    # reasons are two calls; the same output recorded again is a replay (DUPLICATE). Two real calls
    # with identical output can be told apart with the optional ``call_ref``.
    key = "|".join(str(call[k]) for k in sorted(call)) + "|" + inputs.get("call_ref", "")
    call["call_id"] = "c" + hashlib.sha256(key.encode()).hexdigest()[:12]

    def build(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
        if any(r.get("type") == "call" and r["call_id"] == call["call_id"] for r in rows):
            return None
        return {"type": "call", **call}

    return append_checked(path, build)


def _check_event(rows: list[dict[str, Any]], ev: dict[str, Any]) -> None:
    calls = {r["call_id"]: r for r in rows if r.get("type") == "call"}
    call = calls.get(ev.get("call_id", ""))
    if call is None:
        raise OutcomesError("OUTCOME_UNKNOWN_CALL", "no such call in the ledger")
    kind = ev["type"]
    if kind == "observed" and not (ev.get("start") and ev.get("end")):
        raise OutcomesError("OUTCOME_INVALID", "observed needs start and end")
    if kind == "finding":
        if ev.get("source") not in SOURCES or not ev.get("finder") or not ev.get("root"):
            raise OutcomesError("OUTCOME_INVALID", "finding needs a known source, finder and root")
    if kind == "confirmation":
        _check_confirmation(rows, call, ev)


def _check_confirmation(
    rows: list[dict[str, Any]], call: dict[str, Any], ev: dict[str, Any]
) -> None:
    finding = next(
        (
            r
            for r in rows
            if r.get("type") == "finding"
            and r["call_id"] == ev["call_id"]
            and r["finding_id"] == ev.get("finding_id")
        ),
        None,
    )
    if finding is None:
        raise OutcomesError("OUTCOME_UNKNOWN_FINDING", "confirmation names no finding of this call")
    allowed = VERIFIED_KINDS if call["verdict"] == "VERIFIED" else OTHER_KINDS
    if ev.get("outcome") not in allowed:
        raise OutcomesError("OUTCOME_KIND_INVALID", f"{call['verdict']} takes one of {allowed}")
    who, evidence = ev.get("confirmer", ""), ev.get("evidence", "")
    if not who or not evidence:
        raise OutcomesError("OUTCOME_INVALID", "confirmation needs a confirmer and evidence")
    finder = finding["finder"]
    if who == finder or who in finding.get("clones", []) or finder in ev.get("clones", []):
        raise OutcomesError("SAME_CONFIRMER", "the finder or its clone cannot confirm")


def add_event(path: Path, call_id: str, event: dict[str, Any]) -> str:
    """Append one follow-up event for a recorded call. The call line is never touched."""
    if event.get("type") not in EVENT_TYPES:
        raise OutcomesError("OUTCOME_INVALID", f"type must be one of {EVENT_TYPES}")
    ev = {**event, "call_id": call_id}

    def build(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
        _check_event(rows, ev)
        if ev["type"] == "finding":
            ev["finding_id"] = f"f{sum(1 for r in rows if r.get('type') == 'finding') + 1}"
        return ev

    return append_checked(path, build)
