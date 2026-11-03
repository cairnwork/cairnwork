"""Notary error types and the schema check (moved out of notary.py for the 400-line cap, #709).

Behaviour is unchanged; ``cairnwork.notary`` re-exports every name here."""

from __future__ import annotations

from pathlib import Path

from cairnwork.notary_schema import MIGRATIONS, SCHEMA_VERSION


class NotaryError(Exception):
    code = "NOTARY_ERROR"

    def __init__(self, detail: str = "") -> None:
        super().__init__(f"{self.code}: {detail}" if detail else self.code)


class AttemptRequestMismatch(NotaryError):
    code = "ATTEMPT_REQUEST_MISMATCH"


class NotaryInputError(NotaryError):
    code = "NOTARY_INPUT_ERROR"


class NotarySchemaMismatch(NotaryError):
    code = "NOTARY_SCHEMA_MISMATCH"


class NotarySchemaOlder(NotarySchemaMismatch):
    """An older schema is never migrated on open (#343): that would be a one-way write that
    an older deploy pinned to this state could no longer read. notary_migrate does it."""

    code = "NOTARY_SCHEMA_OLDER"


def schema_refusal(
    path: Path, version: int, has_table: bool, objects: bool
) -> NotarySchemaMismatch | None:
    """Why the database at path is no notary a run may open, else None: a current notary, or a
    blank database (version 0, no schema object) that a run makes one. Read by notary_migrate."""
    if not has_table:  # not ours unless blank: never stamped as a notary (#367)
        if version or objects:
            return NotarySchemaMismatch(f"user_version {version} but no notary table")
        return None
    if version in MIGRATIONS:  # nothing written: migration is an explicit step (#343)
        return NotarySchemaOlder(
            f"user_version {version}, need {SCHEMA_VERSION}; back up and migrate with"
            f" cairnwork-verify --state {Path(path).parent} migrate-notary"
        )
    if version != SCHEMA_VERSION:
        return NotarySchemaMismatch(f"user_version {version}, need {SCHEMA_VERSION}")
    return None
