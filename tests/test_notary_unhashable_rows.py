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


# --- #1574: the deep case above passes without the fix on Python 3.14 (its encoder copes with
# 3000 nested lists), so pin the RecursionError branch on its own -----------------------------


def _nest_dict(depth: int) -> str:
    return '{"a":' * depth + "1" + "}" * depth


def _depth_that_parses_but_cannot_be_dumped() -> int | None:
    """Smallest probed depth the C decoder reads but the canonical encoder cannot re-serialise
    on this interpreter (None where the decoder gives up first, e.g. 3.11)."""
    depth = 1000
    while depth <= 400_000:
        try:
            value = json.loads(_nest_dict(depth))
        except RecursionError:
            return None
        try:
            json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
        except RecursionError:
            return depth
        depth *= 2
    return None


def test_a_recursion_error_while_hashing_reads_as_not_verified(
    repo, tmp_path, notarized, monkeypatch
):
    import cairnwork.notary_verify as notary_verify

    def boom(_value):
        raise RecursionError("maximum recursion depth exceeded")

    monkeypatch.setattr(notary_verify, "_digest", boom)
    verdict = notarized.verdict("task-1", "a1", expected_candidate=head_of(repo))
    assert verdict is not None and not verdict.verified
    change = read_change_set(repo, git(repo, "rev-parse", "HEAD~1"), head_of(repo))
    assert notarized.bound_to("task-1", "a1", change) is False


def test_a_dict_nested_past_the_encoder_but_within_the_parser_is_not_verified(
    repo, tmp_path, notarized
):
    depth = _depth_that_parses_but_cannot_be_dumped()
    if depth is None:  # this interpreter's parser refuses first; the stubbed test above covers it
        return
    _mutate(
        tmp_path,
        "request_json",
        lambda text: json.dumps({**json.loads(text), "zz": "MARK"}).replace(
            '"MARK"', _nest_dict(depth)
        ),
    )
    verdict = notarized.verdict("task-1", "a1", expected_candidate=head_of(repo))
    assert verdict is not None and not verdict.verified
