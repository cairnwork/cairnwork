"""Original acceptance evidence (#772 P0-A): what the notary row said, kept apart from the later
evidence that it was wrong. ``origin`` is derived from the row, never typed by the caller, and is
re-checked on read, so a ledger replaced under the same task/attempt ids is seen."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from cairnwork_attest.record import AttestError, VerdictRecord, read_record

ROW_COLUMNS = ("task_id", "attempt_id", "base_commit", "candidate_commit", "request_hash",
               "request_json", "http_status", "status", "integrity", "response_json",
               "recorded_at", "binding_json")  # fmt: skip
EVIDENCE_KEYS = ("candidate", "task_id", "attempt_id", "kind", "ref")


class OriginError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


def _sha(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _accepted(rec: VerdictRecord) -> bool:
    return (rec.status, rec.integrity, rec.http_status) == ("VERIFIED", "VALID", 200)


def origin_of(rec: VerdictRecord) -> dict[str, Any]:
    response: dict[str, Any] = rec.response if isinstance(rec.response, dict) else {}
    hashes = response.get("hashes")
    contract = hashes.get("acceptance_contract_hash") if isinstance(hashes, dict) else None
    return {
        "row_sha256": _sha({k: getattr(rec, k) for k in ROW_COLUMNS}),
        "request_hash": rec.request_hash,
        "status": rec.status,
        "integrity": rec.integrity,
        "http_status": rec.http_status,
        "recorded_at": rec.recorded_at,
        "accepted": _accepted(rec),
        "core_identity_sha256": _sha(response.get("core_identity")),
        "acceptance_contract_hash": contract,
        "binding_sha256": None if rec.binding is None else _sha(rec.binding),
    }


def read_origin(notary_db: Path, task_id: str, attempt_id: str) -> tuple[VerdictRecord, dict]:
    try:
        rec = read_record(notary_db, task_id, attempt_id)
    except AttestError as exc:
        raise OriginError("CASE_RECORD_MISMATCH", f"ledger row: {exc.code}") from exc
    return rec, origin_of(rec)


def check_kind(kind: str, origin: dict[str, Any]) -> None:
    """accepted_bad needs an accepted original verdict; refused_good needs a refused one."""
    if (kind == "accepted_bad") != origin["accepted"]:
        said = "accepted" if origin["accepted"] else f"refused ({origin['status']})"
        raise OriginError("CASE_ORIGIN_MISMATCH", f"original verdict was {said}, case is {kind}")


def read_after(evidence_file: Path, case: dict[str, str]) -> dict[str, str]:
    """The later evidence must name this case; its digest is kept. Truth is the confirmer's job."""
    try:
        raw = Path(evidence_file).read_bytes()
        data = json.loads(raw)
    except (OSError, ValueError) as exc:
        raise OriginError("CASE_EVIDENCE_MISMATCH", "evidence file unreadable") from exc
    if not isinstance(data, dict) or any(not isinstance(data.get(k), str) for k in EVIDENCE_KEYS):
        raise OriginError("CASE_EVIDENCE_MISMATCH", f"evidence needs {', '.join(EVIDENCE_KEYS)}")
    for key in ("candidate", "task_id", "attempt_id", "kind"):
        if data[key] != case[key]:
            raise OriginError("CASE_EVIDENCE_MISMATCH", f"evidence {key} is not the case's")
    return {"ref": data["ref"], "sha256": hashlib.sha256(raw).hexdigest()}


def origin_still_holds(notary_db: Path, case: dict[str, Any]) -> bool:
    try:
        _, now = read_origin(notary_db, case["task_id"], case["attempt_id"])
    except OriginError:
        return False
    return now == case.get("origin")
