"""Original acceptance evidence (#772 P0-A): what the notary row said, kept apart from the later
evidence that it was wrong. ``origin`` is derived from the row, never typed by the caller, and is
re-checked on read, so a ledger replaced under the same task/attempt ids is seen."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from cairnwork.notary_verify import _is_verified
from cairnwork_attest.record import AttestError, VerdictRecord, check_record_bindings, read_record

ROW_COLUMNS = ("task_id", "attempt_id", "base_commit", "candidate_commit", "request_hash",
               "request_json", "http_status", "status", "integrity", "response_json",
               "recorded_at", "binding_json")  # fmt: skip
REFUSALS = ("FAILED", "REJECTED", "REFUSED")  # a Core verdict; error rows (502, CORE_*) are not
EVIDENCE_KEYS = ("candidate", "task_id", "attempt_id", "kind", "ref")


class OriginError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


def _sha(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _accepted(rec: VerdictRecord) -> bool:
    """The notary's own verified rule (status, integrity, HTTP, request hash, echoed hashes)."""
    return _is_verified(rec.http_status, rec.status, rec.integrity, rec.response, rec.request,
                        rec.request_hash)  # fmt: skip


def _refused(rec: VerdictRecord) -> bool:
    """A real Core refusal: a verification with integrity VALID and a refusal status."""
    judged = isinstance(rec.response, dict) and isinstance(rec.response.get("verification"), dict)
    return judged and rec.http_status == 200 and rec.integrity == "VALID" and (
        rec.status in REFUSALS)  # fmt: skip


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
        "refused": _refused(rec),
        "core_identity_sha256": _sha(response.get("core_identity")),
        "acceptance_contract_hash": contract,
        "binding_sha256": None if rec.binding is None else _sha(rec.binding),
    }


def read_origin(notary_db: Path, task_id: str, attempt_id: str) -> tuple[VerdictRecord, dict]:
    try:
        rec = read_record(notary_db, task_id, attempt_id)
        check_record_bindings(rec)  # request hash and target revision agree with the columns
    except AttestError as exc:
        raise OriginError("CASE_RECORD_MISMATCH", f"ledger row: {exc.code}") from exc
    return rec, origin_of(rec)


def check_kind(kind: str, origin: dict[str, Any]) -> None:
    """accepted_bad needs an accepted original verdict; refused_good needs a refused one."""
    if not origin["accepted" if kind == "accepted_bad" else "refused"]:
        said = f"{origin['status']}/{origin['integrity']}/{origin['http_status']}"
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
    path = str(Path(evidence_file).resolve())
    return {"ref": data["ref"], "file": path, "sha256": hashlib.sha256(raw).hexdigest()}


def origin_still_holds(notary_db: Path, case: dict[str, Any]) -> bool:
    """The row still gives the stored origin and the later evidence file is unchanged."""
    after = case.get("after")
    if not isinstance(after, dict):
        return False
    try:
        if hashlib.sha256(Path(str(after.get("file"))).read_bytes()).hexdigest() != after.get(
            "sha256"
        ):
            return False
    except OSError:
        return False
    try:
        _, now = read_origin(notary_db, case["task_id"], case["attempt_id"])
    except OriginError:
        return False
    return now == case.get("origin")
