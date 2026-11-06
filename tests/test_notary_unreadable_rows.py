"""#909: a stored notary row whose request, response, binding or expected identity JSON is nested
too deep, is not JSON at all, or is JSON but not an object reads back as not verified instead of
raising, and ``bound_to`` answers False."""

from __future__ import annotations

import sqlite3
from dataclasses import replace

import pytest
from conftest import git

from cairnwork.changeset import read_change_set
from cairnwork.notary import Notary
from tests.test_notary import head_of, notarize

DEPTH = 100_000  # past every recursion limit on 3.11-3.13 for the C json decoder
DEEP_TEXT = "[" * DEPTH + "]" * DEPTH


def _rewrite(tmp_path, column: str, value: str) -> None:
    conn = sqlite3.connect(tmp_path / "notary.sqlite")
    conn.execute("DROP TRIGGER core_verifications_no_update")
    conn.execute(f"UPDATE core_verifications SET {column} = ?", (value,))  # noqa: S608 - test
    conn.commit()
    conn.close()


@pytest.fixture
def notarized(repo, tmp_path):
    notary = Notary(tmp_path / "notary.sqlite")
    assert notarize(notary, repo, {"src/a.py": "x = 2\n"}).verified
    return notary


def _change(repo):
    return read_change_set(repo, git(repo, "rev-parse", "HEAD~1"), head_of(repo))


@pytest.mark.parametrize(
    "column", ["request_json", "response_json", "binding_json", "expected_identity"]
)
@pytest.mark.parametrize(
    "value", [DEEP_TEXT, "{not json", "[1]", "null"], ids=["deep", "malformed", "list", "null"]
)
def test_an_unreadable_stored_row_is_not_verified(repo, tmp_path, notarized, column, value):
    _rewrite(tmp_path, column, value)
    verdict = notarized.verdict("task-1", "a1", expected_candidate=head_of(repo))
    assert verdict is not None and not verdict.verified


def test_bound_to_is_false_for_an_unreadable_stored_request(repo, tmp_path, notarized):
    assert notarized.bound_to("task-1", "a1", _change(repo)) is True
    _rewrite(tmp_path, "request_json", DEEP_TEXT)
    assert notarized.bound_to("task-1", "a1", _change(repo)) is False


def test_bound_to_is_false_when_the_request_became_unreadable_after_the_read(
    repo, tmp_path, notarized, monkeypatch
):
    good = notarized.verdict("task-1", "a1", expected_candidate=head_of(repo))
    assert good is not None and good.verified
    _rewrite(tmp_path, "request_json", DEEP_TEXT)  # a writer between _read and the second read
    monkeypatch.setattr(notarized, "_read", lambda *_a: replace(good))
    assert notarized.bound_to("task-1", "a1", _change(repo)) is False


@pytest.mark.parametrize("value", ["[1]", "null", '"text"'], ids=["list", "null", "string"])
def test_bound_to_is_false_for_a_stored_request_that_is_not_an_object(
    repo, tmp_path, notarized, monkeypatch, value
):
    good = notarized.verdict("task-1", "a1", expected_candidate=head_of(repo))
    assert good is not None and good.verified
    _rewrite(tmp_path, "request_json", value)
    monkeypatch.setattr(notarized, "_read", lambda *_a: replace(good))
    assert notarized.bound_to("task-1", "a1", _change(repo)) is False
