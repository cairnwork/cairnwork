"""Casebook file: JSON lines, each carrying `prev` = sha256 of the previous line's bytes.

The chain shows an edited or removed line except the last; ``<casebook>.head`` holds the last
line's digest, so removing the tail (or the whole file) is seen too. ``add`` holds an exclusive
lock on ``<casebook>.lock`` while it reads, checks and appends, so two adds never fork the chain.
A crash between the append and the head update leaves them disagreeing: refused, fail closed.
This shows accidental edits; someone who rewrites the file and the head together is not caught.

#772: a case line carries ``origin`` (derived from the notary row, origin.py) and ``after`` (the
later evidence), kept apart. ``"type": "confirmation"`` lines name a case line by its digest; a
case's state is computed on read and only ``confirmed`` cases whose origin re-checks are eligible.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from experimental.acceptance_casebook.identity import confirmation_problem
from experimental.acceptance_casebook.origin import (
    OriginError,
    check_kind,
    origin_still_holds,
    read_after,
    read_origin,
)

T = TypeVar("T")
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
    "evidence_file",
)
STORED = tuple(k for k in FIELDS if k != "evidence_file")


class CasebookError(Exception):
    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        super().__init__(f"{code}: {message}" if message else code)


def _head(path: Path) -> Path:
    return path.with_name(path.name + ".head")


def _read_chain(path: Path) -> tuple[list[tuple[dict[str, Any], str]], str]:
    """Return ([(entry, its line digest)], digest of last line). Any inconsistency is
    NOT_APPEND_ONLY."""
    cases, prev = _read_lines(path)
    head = _head(path)
    recorded = head.read_text().strip() if head.exists() else ZERO
    if recorded != prev:
        raise CasebookError("CASEBOOK_NOT_APPEND_ONLY", "last line differs from the recorded head")
    return cases, prev


def _read_lines(path: Path) -> tuple[list[tuple[dict[str, Any], str]], str]:
    if not path.exists():
        return [], ZERO
    data = path.read_bytes()
    lines = data.splitlines(keepends=True)
    prev = ZERO
    cases: list[tuple[dict[str, Any], str]] = []
    if data and not data.endswith(b"\n"):
        raise CasebookError("CASEBOOK_NOT_APPEND_ONLY", "truncated last line")
    for number, raw in enumerate(lines, 1):
        try:
            entry = json.loads(raw)
        except ValueError as exc:
            raise CasebookError("CASEBOOK_NOT_APPEND_ONLY", f"line {number} unreadable") from exc
        if not isinstance(entry, dict) or entry.get("prev") != prev:
            raise CasebookError("CASEBOOK_NOT_APPEND_ONLY", f"line {number} chain broken")
        prev = hashlib.sha256(raw).hexdigest()
        cases.append((entry, prev))
    return cases, prev


def _is_case(entry: dict[str, Any]) -> bool:
    return entry.get("type", "case") == "case"  # #763 lines have no type


def _state(case: dict[str, Any], digest: str, entries: list[tuple[dict[str, Any], str]]) -> str:
    said = [e.get("conclusion") for e, _ in entries
            if e.get("type") == "confirmation" and e.get("case") == digest
            and confirmation_problem(case, e) is None]  # fmt: skip
    if "rejected" in said:
        return "rejected"
    return "confirmed" if "confirmed" in said else "unconfirmed"


def list_cases(path: Path, notary_db: Path | None = None) -> list[dict[str, Any]]:
    """Cases with their computed ``state``; with ``notary_db`` a case whose row no longer gives
    the recorded origin is ``origin_changed``."""
    entries = _read_chain(path)[0]
    out = []
    for entry, digest in entries:
        if not _is_case(entry):
            continue
        state = _state(entry, digest, entries)
        if notary_db is not None and not origin_still_holds(notary_db, entry):
            state = "origin_changed"
        out.append({**entry, "line_sha256": digest, "state": state})
    return out


def eligible_cases(path: Path, notary_db: Path) -> list[dict[str, Any]]:
    """Only confirmed cases whose original evidence still re-checks count in any comparison."""
    return [c for c in list_cases(path, notary_db) if c["state"] == "confirmed"]


def _bind(case: dict[str, Any], notary_db: Path) -> dict[str, Any]:
    try:
        rec, origin = read_origin(notary_db, case["task_id"], case["attempt_id"])
        if rec.candidate_commit != case["candidate"] or rec.base_commit != case["base"]:
            raise CasebookError("CASE_RECORD_MISMATCH", "candidate or base differs from ledger row")
        check_kind(case["kind"], origin)
        after = read_after(Path(case["evidence_file"]), case)
    except OriginError as exc:
        raise CasebookError(exc.code, str(exc)) from exc
    bound = {**{k: case[k] for k in STORED}, "type": "case", "origin": origin, "after": after}
    return {**bound, "adder": case["adder"]} if case.get("adder") else bound


def add_case(path: Path, notary_db: Path, case: dict[str, Any]) -> str:
    """Append one case. Returns "ADDED" or "DUPLICATE"; raises CasebookError on refusal."""
    if case.get("kind") not in KINDS or any(not case.get(k) for k in FIELDS):
        raise CasebookError("CASE_INVALID", "missing field or unknown kind")
    return locked(path, lambda: _add_locked(path, notary_db, case))


def locked(path: Path, action: Callable[[], T]) -> T:
    lock = os.open(path.with_name(path.name + ".lock"), os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return action()
    finally:
        os.close(lock)  # closing drops the lock


def _add_locked(path: Path, notary_db: Path, case: dict[str, Any]) -> str:
    entries, prev = _read_chain(path)
    bound = _bind(case, notary_db)
    key = (case["candidate"], case["base"], case["version"])
    for old in (e for e, _ in entries if _is_case(e)):
        if (old["candidate"], old["base"], old["version"]) != key:
            continue
        if old["kind"] != case["kind"]:
            raise CasebookError("CASE_CONFLICT", "same case recorded with the other kind")
        if old["attempt_id"] == case["attempt_id"]:  # another attempt is its own line
            return "DUPLICATE"
    _append(path, bound, prev)
    return "ADDED"


def _append(path: Path, entry: dict[str, Any], prev: str) -> str:
    """Append one line after ``prev`` and move the head; the caller holds the lock."""
    line = json.dumps({**entry, "prev": prev}, sort_keys=True) + "\n"
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(fd, "ab") as handle:
        handle.write(line.encode())
    tmp = path.with_name(path.name + ".head.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(hashlib.sha256(line.encode()).hexdigest() + "\n")
    os.replace(tmp, _head(path))
    return hashlib.sha256(line.encode()).hexdigest()
