"""Ledger file: JSON lines, each carrying `prev` = sha256 of the previous line's bytes.

``<ledger>.head`` holds the last line's digest, so removing the tail (or the whole file) is seen
too. Appends hold an exclusive lock on ``<ledger>.lock`` while they read, check and write. A crash
between the append and the head update leaves them disagreeing: refused, fail closed. This shows
accidental edits; someone who rewrites the file and the head together is not caught.
(Same pattern as the casebook in #763; to be deduplicated after it merges.)
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

ZERO = "0" * 64
NOT_APPEND_ONLY = "OUTCOMES_NOT_APPEND_ONLY"


class OutcomesError(Exception):
    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        super().__init__(f"{code}: {message}" if message else code)


def _head(path: Path) -> Path:
    return path.with_name(path.name + ".head")


def _read_lines(path: Path) -> tuple[list[dict[str, Any]], str]:
    if not path.exists():
        return [], ZERO
    data = path.read_bytes()
    if data and not data.endswith(b"\n"):
        raise OutcomesError(NOT_APPEND_ONLY, "truncated last line")
    prev, rows = ZERO, []
    for number, raw in enumerate(data.splitlines(keepends=True), 1):
        try:
            entry = json.loads(raw)
        except ValueError as exc:
            raise OutcomesError(NOT_APPEND_ONLY, f"line {number} unreadable") from exc
        if not isinstance(entry, dict) or entry.get("prev") != prev:
            raise OutcomesError(NOT_APPEND_ONLY, f"line {number} chain broken")
        rows.append(entry)
        prev = hashlib.sha256(raw).hexdigest()
    return rows, prev


def read_ledger(path: Path) -> list[dict[str, Any]]:
    """All lines, or OUTCOMES_NOT_APPEND_ONLY: never a partial result."""
    rows, prev = _read_lines(path)
    head = _head(path)
    if (head.read_text().strip() if head.exists() else ZERO) != prev:
        raise OutcomesError(NOT_APPEND_ONLY, "last line differs from the recorded head")
    return rows


def append_checked(
    path: Path, build: Callable[[list[dict[str, Any]]], dict[str, Any] | None]
) -> str:
    """Under the lock: read the chain, let ``build(rows)`` return the new line (without ``prev``)
    or None for a duplicate, then append. Returns "ADDED" or "DUPLICATE"."""
    lock = os.open(path.with_name(path.name + ".lock"), os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        rows, prev = _read_lines(path)
        head = _head(path)
        if (head.read_text().strip() if head.exists() else ZERO) != prev:
            raise OutcomesError(NOT_APPEND_ONLY, "last line differs from the recorded head")
        new = build(rows)
        if new is None:
            return "DUPLICATE"
        line = (json.dumps({**new, "prev": prev}, sort_keys=True) + "\n").encode()
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "ab") as handle:
            handle.write(line)
        tmp = path.with_name(path.name + ".head.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(hashlib.sha256(line).hexdigest() + "\n")
        os.replace(tmp, head)
        return "ADDED"
    finally:
        os.close(lock)
