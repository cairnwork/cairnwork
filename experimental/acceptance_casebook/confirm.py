"""Independent confirmation (#772 AC1b): a ``"type": "confirmation"`` line naming one case line by
its sha256, with the confirmer's identity, the conclusion and the independent check's evidence.
Refused unless identity.confirmation_problem passes; the reader checks the same rule again."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from experimental.acceptance_casebook.identity import IdentityError, confirmation_problem, file_sha
from experimental.acceptance_casebook.store import (
    CasebookError,
    _append,
    _is_case,
    _read_chain,
    locked,
)


def check_record(method: str, evidence_file: str) -> dict[str, str]:
    try:
        digest = file_sha(evidence_file) or ""
    except IdentityError as exc:
        raise CasebookError("NO_CHECK_EVIDENCE", "check evidence file unreadable or empty") from exc
    evidence_file = str(Path(evidence_file).resolve())
    return {"method": method, "evidence_ref": evidence_file, "evidence_sha256": digest}


def confirm_case(
    path: Path, case_sha256: str, confirmer: dict[str, Any], conclusion: str, check: dict
) -> str:
    """Append one confirmation; returns its line digest. Raises CasebookError on refusal."""
    return locked(path, lambda: _confirm_locked(path, case_sha256, confirmer, conclusion, check))


def _confirm_locked(
    path: Path, case_sha256: str, confirmer: dict[str, Any], conclusion: str, check: dict
) -> str:
    entries, prev = _read_chain(path)
    cases = [e for e, d in entries if d == case_sha256 and _is_case(e)]
    if not cases:
        raise CasebookError("CASE_NOT_FOUND", "no case line with that sha256")
    event = {"type": "confirmation", "case": case_sha256, "confirmer": confirmer,
             "conclusion": conclusion, "check": check}  # fmt: skip
    problem = confirmation_problem(cases[0], event)
    if problem:
        raise CasebookError(problem, "confirmation refused")
    return _append(path, event, prev)
