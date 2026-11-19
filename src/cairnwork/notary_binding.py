"""What a notary row says about its own binding, for readers outside the notary (#935).

``Notary.verdict`` already folds the Core identity check, the key and commit column check and the
expected-candidate check into ``verified``. A reader that must ask the same questions of a row
that is not VERIFIED (a FAILED row from a mismatching Core, an identity rewritten after the fact)
gets the answers here, from the notary's own code, instead of a copy of its rules.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from cairnwork.notary_verify import _columns_match

IDENTITY_UNREADABLE = "IDENTITY_UNREADABLE"  # the identity to compare with could not be resolved


@dataclass(frozen=True)
class RowBinding:
    expected_identity: str | None  # the stored column (schema 2+); None for an older row
    identity_problem: str  # "" when the response's Core identity is acceptable, else the reason
    columns_match: bool  # task, attempt, commits and binding agree with the stored request


def row_binding(notary: Any, task_id: str, attempt_id: str) -> RowBinding | None:
    """The binding facts of one row regardless of its status; None when there is no such row."""
    conn = notary._connect()  # noqa: SLF001 - the notary's own helper, same package
    try:
        row = conn.execute(
            "SELECT base_commit, candidate_commit, request_json, response_json,"
            " expected_identity, binding_json FROM core_verifications"
            " WHERE task_id = ? AND attempt_id = ?",
            (task_id, attempt_id),
        ).fetchone()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    if row is None:
        return None
    base, candidate, request_json, response_json, expected, binding_json = row
    try:
        binding = None if binding_json is None else json.loads(binding_json)
        request, response = json.loads(request_json), json.loads(response_json)
        if not (isinstance(request, dict) and isinstance(response, dict)) or not (
            binding is None or isinstance(binding, dict)
        ):
            raise ValueError("a stored request, response or binding that is not an object")
    except (ValueError, RecursionError):  # a row too deep or malformed to read: never bound
        return RowBinding(expected, IDENTITY_UNREADABLE, False)
    try:
        problem = notary._identity_problem(request, response, expected)  # noqa: SLF001
    except ImportError:  # no local Core installed to compare an older (NULL) row with
        problem = IDENTITY_UNREADABLE
    return RowBinding(
        expected, problem, _columns_match(request, task_id, attempt_id, base, candidate, binding)
    )
