"""#923: a stored request that parses as JSON but cannot be re-serialised for hashing
(a non-finite number, or nesting past the canonical encoder's recursion limit but within the
parser's) reads back as not verified instead of raising, and ``bound_to`` answers False."""

from __future__ import annotations

import json
import sqlite3

import pytest
from conftest import git

from cairnwork.changeset import read_change_set
from cairnwork.notary import Notary
from tests.test_notary import head_of, notarize

NESTED = 3000  # parses (C decoder), but the Python canonical encoder recurses past its limit


def _mutate(tmp_path, column: str, edit) -> None:
    conn = sqlite3.connect(tmp_path / "notary.sqlite")
    conn.execute("DROP TRIGGER core_verifications_no_update")
    (text,) = conn.execute(f"SELECT {column} FROM core_verifications").fetchone()  # noqa: S608
    conn.execute(f"UPDATE core_verifications SET {column} = ?", (edit(text),))  # noqa: S608
    conn.commit()
    conn.close()


def _nan(text: str) -> str:
    value = json.loads(text)
    value["zz"] = "NAN_MARK"
    return json.dumps(value).replace('"NAN_MARK"', "NaN")


def _deep(text: str) -> str:
    value = json.loads(text)
    value["zz"] = "DEEP_MARK"
    return json.dumps(value).replace('"DEEP_MARK"', "[" * NESTED + "]" * NESTED)


@pytest.fixture
def notarized(repo, tmp_path):
    notary = Notary(tmp_path / "notary.sqlite")
    assert notarize(notary, repo, {"src/a.py": "x = 2\n"}).verified
    return notary


@pytest.mark.parametrize("edit", [_nan, _deep], ids=["nan", "deep"])
def test_a_row_that_parses_but_cannot_be_hashed_is_not_verified(repo, tmp_path, notarized, edit):
    _mutate(tmp_path, "request_json", edit)
    verdict = notarized.verdict("task-1", "a1", expected_candidate=head_of(repo))
    assert verdict is not None and not verdict.verified
    change = read_change_set(repo, git(repo, "rev-parse", "HEAD~1"), head_of(repo))
    assert notarized.bound_to("task-1", "a1", change) is False
