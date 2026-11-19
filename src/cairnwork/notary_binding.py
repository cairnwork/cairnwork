"""What a notary row says about its own binding, for readers outside the notary (#935).

``Notary.verdict`` already folds the Core identity check, the key and commit column check and the
expected-candidate check into ``verified``. A reader that must ask the same questions of a row
that is not VERIFIED (a FAILED row from a mismatching Core, an identity rewritten after the fact)
gets the answers here, from the notary's own code, instead of a copy of its rules. This read uses a
read-only SQLite snapshot and may be older than a later verify. Database read failures raise
``RowBindingUnavailable``; callers must treat that as unbound, while ``None`` means confirmed
absence only.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from cairnwork.notary_verify import _columns_match

IDENTITY_UNREADABLE = "IDENTITY_UNREADABLE"  # the identity to compare with could not be resolved


@dataclass(frozen=True)
class RowBinding:
    # Digest only; raw Core identity may contain hidden build data.
    expected_identity_sha256: str | None
    identity_problem: str  # "" when the response's Core identity is acceptable, else the reason
    columns_match: bool  # task, attempt, commits and binding agree with the stored request


# A stored request, response or binding larger than this is read as unreadable, not parsed: json
# parsing a huge value could exhaust memory. (main's Notary.verify has no such cap: see #1515.)
MAX_STORED_JSON_BYTES = 4 * 1024 * 1024


class RowBindingUnavailable(Exception):
    """The notary row could not be read; consumers must treat it as unbound, never absent."""


def row_binding(notary: Any, task_id: str, attempt_id: str) -> RowBinding | None:
    """Binding facts regardless of status; None means absent, read failures raise."""
    try:
        return _row_binding(notary, task_id, attempt_id)
    except MemoryError:  # anywhere (connect, execute, fetch, close, parse, judge): unreadable
        return RowBinding(None, IDENTITY_UNREADABLE, False)


def _row_binding(notary: Any, task_id: str, attempt_id: str) -> RowBinding | None:
    conn = None
    try:
        # Do not use Notary._connect(): it opens a writable connection and sets write pragmas.
        uri = notary._path.resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=30, isolation_level=None)
        row = conn.execute(
            "SELECT base_commit, candidate_commit,"
            " CASE WHEN length(CAST(request_json AS BLOB)) > :cap"
            " THEN NULL ELSE request_json END,"
            " CASE WHEN length(CAST(response_json AS BLOB)) > :cap"
            " THEN NULL ELSE response_json END,"
            " expected_identity,"
            " CASE WHEN length(CAST(binding_json AS BLOB)) > :cap THEN '' ELSE binding_json END"
            " FROM core_verifications WHERE task_id = :task AND attempt_id = :attempt",
            {"cap": MAX_STORED_JSON_BYTES, "task": task_id, "attempt": attempt_id},
        ).fetchone()
    except sqlite3.Error as exc:
        raise RowBindingUnavailable("cannot read notary row") from exc
    finally:
        if conn is not None:
            try:
                conn.close()
            except sqlite3.Error as exc:
                raise RowBindingUnavailable("cannot close notary read snapshot") from exc
            except MemoryError:  # still release the descriptor, then let the outer level map it
                try:
                    conn.close()
                except (MemoryError, sqlite3.Error):
                    pass
                raise
    if row is None:
        return None
    base, candidate, request_json, response_json, expected, binding_json = row
    try:
        expected_digest = _identity_digest(expected)
        binding = None if binding_json is None else json.loads(binding_json)
        request, response = json.loads(request_json), json.loads(response_json)
        if not (isinstance(request, dict) and isinstance(response, dict)) or not (
            binding is None or isinstance(binding, dict)
        ):
            raise ValueError("a stored request, response or binding that is not an object")
        problem = notary._identity_problem(request, response, expected)  # noqa: SLF001
        columns_match = _columns_match(request, task_id, attempt_id, base, candidate, binding)
    except (  # malformed stored values or judgment helpers that cannot safely interpret them
        TypeError,
        ValueError,
        KeyError,
        AttributeError,
        RecursionError,
        UnicodeError,
        ImportError,
        MemoryError,
    ):
        return RowBinding(_safe_identity_digest(expected), IDENTITY_UNREADABLE, False)
    return RowBinding(expected_digest, problem, columns_match)


def _identity_digest(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError("stored expected identity is not text")
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _safe_identity_digest(value: Any) -> str | None:
    try:
        return _identity_digest(value)
    except (TypeError, UnicodeError, MemoryError):
        return None
