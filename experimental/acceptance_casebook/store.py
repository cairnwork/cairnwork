"""Casebook file: JSON lines, each carrying `prev` = sha256 of the previous line's bytes.

The chain shows an edited or removed line except the last; ``<casebook>.head`` holds the last
line's digest, so removing the tail (or the whole file) is seen too. ``add`` holds an exclusive
lock on ``<casebook>.lock`` while it reads, checks and appends, so two adds never fork the chain.
A crash between the append and the head update leaves them disagreeing: refused, fail closed.
This shows accidental edits; someone who rewrites the file and the head together is not caught.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from cairnwork_attest.record import AttestError, read_record

ZERO = "0" * 64
KINDS = ("accepted_bad", "refused_good")
FIELDS = (
    "candidate",
    "base",
    "version",
    "task_id",
    "attempt_id",
    "kind",
    "found_by",
    "evidence",
)


class CasebookError(Exception):
    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        super().__init__(f"{code}: {message}" if message else code)


def _head(path: Path) -> Path:
    return path.with_name(path.name + ".head")


def _read_chain(path: Path) -> tuple[list[dict[str, Any]], str]:
    """Return (cases, digest of last line). Any inconsistency is NOT_APPEND_ONLY."""
    cases, prev = _read_lines(path)
    head = _head(path)
    recorded = head.read_text().strip() if head.exists() else ZERO
    if recorded != prev:
        raise CasebookError("CASEBOOK_NOT_APPEND_ONLY", "last line differs from the recorded head")
    return cases, prev


def _read_lines(path: Path) -> tuple[list[dict[str, Any]], str]:
    if not path.exists():
        return [], ZERO
    data = path.read_bytes()
    lines = data.splitlines(keepends=True)
    prev = ZERO
    cases: list[dict[str, Any]] = []
    if data and not data.endswith(b"\n"):
        raise CasebookError("CASEBOOK_NOT_APPEND_ONLY", "truncated last line")
    for number, raw in enumerate(lines, 1):
        try:
            entry = json.loads(raw)
        except ValueError as exc:
            raise CasebookError("CASEBOOK_NOT_APPEND_ONLY", f"line {number} unreadable") from exc
        if not isinstance(entry, dict) or entry.get("prev") != prev:
            raise CasebookError("CASEBOOK_NOT_APPEND_ONLY", f"line {number} chain broken")
        cases.append(entry)
        prev = hashlib.sha256(raw).hexdigest()
    return cases, prev


def list_cases(path: Path) -> list[dict[str, Any]]:
    return _read_chain(path)[0]


def _check_record(case: dict[str, str], notary_db: Path) -> None:
    try:
        rec = read_record(notary_db, case["task_id"], case["attempt_id"])
    except AttestError as exc:
        raise CasebookError("CASE_RECORD_MISMATCH", f"ledger row: {exc.code}") from exc
    if rec.candidate_commit != case["candidate"] or rec.base_commit != case["base"]:
        raise CasebookError("CASE_RECORD_MISMATCH", "candidate or base differs from ledger row")


def add_case(path: Path, notary_db: Path, case: dict[str, str]) -> str:
    """Append one case. Returns "ADDED" or "DUPLICATE"; raises CasebookError on refusal."""
    if case.get("kind") not in KINDS or any(not case.get(k) for k in FIELDS):
        raise CasebookError("CASE_INVALID", "missing field or unknown kind")
    lock = os.open(path.with_name(path.name + ".lock"), os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _add_locked(path, notary_db, case)
    finally:
        os.close(lock)  # closing drops the lock


def _add_locked(path: Path, notary_db: Path, case: dict[str, str]) -> str:
    cases, prev = _read_chain(path)
    _check_record(case, notary_db)
    key = (case["candidate"], case["base"], case["version"])
    for old in cases:
        if (old["candidate"], old["base"], old["version"]) == key:
            if old["kind"] == case["kind"]:
                return "DUPLICATE"
            raise CasebookError("CASE_CONFLICT", "same case recorded with the other kind")
    line = json.dumps({**{k: case[k] for k in FIELDS}, "prev": prev}, sort_keys=True) + "\n"
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(fd, "ab") as handle:
        handle.write(line.encode())
    tmp = path.with_name(path.name + ".head.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(hashlib.sha256(line.encode()).hexdigest() + "\n")
    os.replace(tmp, _head(path))
    return "ADDED"
