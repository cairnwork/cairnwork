"""#909: a stored notary row whose request, response, binding or expected identity JSON is nested
too deep, is not JSON at all, or is JSON but not an object reads back as not verified instead of
raising, and ``bound_to`` answers False."""

from __future__ import annotations

import json
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


@pytest.mark.parametrize("column", ["request_json", "response_json", "binding_json"])
def test_oversized_stored_json_is_not_parsed_or_verified(
    repo, tmp_path, notarized, monkeypatch, column
):
    import cairnwork.notary as notary_module
    from cairnwork.notary_binding import MAX_STORED_JSON_BYTES

    parsed: list[int] = []
    real = notary_module.json.loads
    monkeypatch.setattr(
        notary_module.json,
        "loads",
        lambda value, *args, **kwargs: (parsed.append(len(value)), real(value, *args, **kwargs))[1],
    )
    _rewrite(tmp_path, column, '{"pad":"' + "x" * MAX_STORED_JSON_BYTES + '"}')
    verdict = notarized.verdict("task-1", "a1", expected_candidate=head_of(repo))
    assert verdict is not None and not verdict.verified
    assert all(size <= MAX_STORED_JSON_BYTES for size in parsed)


def test_bound_to_does_not_parse_oversized_second_read(repo, tmp_path, notarized, monkeypatch):
    import cairnwork.notary as notary_module
    from cairnwork.notary_binding import MAX_STORED_JSON_BYTES

    good = notarized.verdict("task-1", "a1", expected_candidate=head_of(repo))
    assert good is not None and good.verified
    real = notary_module.json.loads
    parsed: list[int] = []
    monkeypatch.setattr(
        notary_module.json,
        "loads",
        lambda value, *args, **kwargs: (parsed.append(len(value)), real(value, *args, **kwargs))[1],
    )
    _rewrite(
        tmp_path,
        "request_json",
        '{"pad":"' + "x" * MAX_STORED_JSON_BYTES + '"}',
    )
    # The first read is stubbed to a VERIFIED verdict, so bound_to reaches its own second read.
    monkeypatch.setattr(notarized, "_read", lambda *_a: replace(good))
    assert notarized.bound_to("task-1", "a1", _change(repo)) is False
    assert parsed, "the second read must reach json.loads"
    assert all(size <= MAX_STORED_JSON_BYTES for size in parsed)


@pytest.mark.parametrize("value", ["[1]", "null", '"text"'], ids=["list", "null", "string"])
def test_bound_to_is_false_for_a_stored_request_that_is_not_an_object(
    repo, tmp_path, notarized, monkeypatch, value
):
    good = notarized.verdict("task-1", "a1", expected_candidate=head_of(repo))
    assert good is not None and good.verified
    _rewrite(tmp_path, "request_json", value)
    monkeypatch.setattr(notarized, "_read", lambda *_a: replace(good))
    assert notarized.bound_to("task-1", "a1", _change(repo)) is False


# --- #1574: pin the exact byte cap of #1544 (">" not ">=", bytes not characters) -------------


def _padded(total_bytes: int, filler: str = "x") -> str:
    """A JSON object text of exactly ``total_bytes`` UTF-8 bytes (``filler`` is 1 or 2 bytes)."""
    head, tail = '{"pad":"', '"}'
    room = total_bytes - len(head) - len(tail)
    width = len(filler.encode())
    assert room % width == 0
    return head + filler * (room // width) + tail


def _parsed_sizes(monkeypatch) -> list[int]:
    sizes: list[int] = []
    real = json.loads
    monkeypatch.setattr(
        json, "loads", lambda v, *a, **k: (sizes.append(len(v.encode())), real(v, *a, **k))[1]
    )
    return sizes


@pytest.mark.parametrize(
    "column", ["request_json", "response_json", "binding_json", "expected_identity"]
)
def test_a_stored_json_of_exactly_the_cap_is_still_read(
    repo, tmp_path, notarized, monkeypatch, column
):
    from cairnwork.notary_binding import MAX_STORED_JSON_BYTES

    sizes = _parsed_sizes(monkeypatch)
    _rewrite(tmp_path, column, _padded(MAX_STORED_JSON_BYTES))
    notarized.verdict("task-1", "a1", expected_candidate=head_of(repo))
    assert MAX_STORED_JSON_BYTES in sizes  # at the cap it is read (">" not ">=")


@pytest.mark.parametrize(
    "column", ["request_json", "response_json", "binding_json", "expected_identity"]
)
def test_a_stored_json_one_byte_over_the_cap_is_withheld(
    repo, tmp_path, notarized, monkeypatch, column
):
    from cairnwork.notary_binding import MAX_STORED_JSON_BYTES

    sizes = _parsed_sizes(monkeypatch)
    _rewrite(tmp_path, column, _padded(MAX_STORED_JSON_BYTES + 1))
    verdict = notarized.verdict("task-1", "a1", expected_candidate=head_of(repo))
    assert verdict is not None and not verdict.verified
    assert MAX_STORED_JSON_BYTES + 1 not in sizes


@pytest.mark.parametrize(
    "column", ["request_json", "response_json", "binding_json", "expected_identity"]
)
def test_the_cap_counts_bytes_not_characters(repo, tmp_path, notarized, monkeypatch, column):
    from cairnwork.notary_binding import MAX_STORED_JSON_BYTES

    text = _padded(MAX_STORED_JSON_BYTES + 2, "é")  # 2 bytes each: under the cap in characters
    assert len(text) <= MAX_STORED_JSON_BYTES < len(text.encode())
    sizes = _parsed_sizes(monkeypatch)
    _rewrite(tmp_path, column, text)
    verdict = notarized.verdict("task-1", "a1", expected_candidate=head_of(repo))
    assert verdict is not None and not verdict.verified
    assert len(text.encode()) not in sizes


def _bound_to_sizes(repo, tmp_path, notarized, monkeypatch, text):
    good = notarized.verdict("task-1", "a1", expected_candidate=head_of(repo))
    assert good is not None and good.verified
    sizes = _parsed_sizes(monkeypatch)
    _rewrite(tmp_path, "request_json", text)
    monkeypatch.setattr(notarized, "_read", lambda *_a: replace(good))  # reach the 2nd read
    assert notarized.bound_to("task-1", "a1", _change(repo)) is False
    return sizes


def test_bound_to_reads_a_request_of_exactly_the_cap(repo, tmp_path, notarized, monkeypatch):
    from cairnwork.notary_binding import MAX_STORED_JSON_BYTES

    sizes = _bound_to_sizes(repo, tmp_path, notarized, monkeypatch, _padded(MAX_STORED_JSON_BYTES))
    assert MAX_STORED_JSON_BYTES in sizes


@pytest.mark.parametrize("filler,extra", [("x", 1), ("é", 2)], ids=["one-byte-over", "bytes"])
def test_bound_to_withholds_one_byte_over_and_counts_bytes(
    repo, tmp_path, notarized, monkeypatch, filler, extra
):
    from cairnwork.notary_binding import MAX_STORED_JSON_BYTES

    text = _padded(MAX_STORED_JSON_BYTES + extra, filler)
    sizes = _bound_to_sizes(repo, tmp_path, notarized, monkeypatch, text)
    assert len(text.encode()) not in sizes


def test_row_binding_reads_an_oversized_identity_as_unreadable(
    repo, tmp_path, notarized, monkeypatch
):
    from cairnwork.notary_binding import IDENTITY_UNREADABLE, MAX_STORED_JSON_BYTES

    sizes = _parsed_sizes(monkeypatch)
    text = _padded(MAX_STORED_JSON_BYTES + 2, "é")  # under the cap in characters, over in bytes
    assert len(text) <= MAX_STORED_JSON_BYTES < len(text.encode())
    _rewrite(tmp_path, "expected_identity", text)
    binding = notarized.row_binding("task-1", "a1")
    assert binding is not None
    assert binding.identity_problem == IDENTITY_UNREADABLE and not binding.columns_match
    assert binding.expected_identity_sha256 is None
    assert all(size <= MAX_STORED_JSON_BYTES for size in sizes)


def test_row_binding_reads_an_identity_of_exactly_the_cap(repo, tmp_path, notarized, monkeypatch):
    import hashlib

    from cairnwork.notary_binding import IDENTITY_UNREADABLE, MAX_STORED_JSON_BYTES

    sizes = _parsed_sizes(monkeypatch)
    text = _padded(MAX_STORED_JSON_BYTES)
    _rewrite(tmp_path, "expected_identity", text)
    binding = notarized.row_binding("task-1", "a1")
    assert binding is not None
    assert binding.expected_identity_sha256 == hashlib.sha256(text.encode()).hexdigest()
    assert MAX_STORED_JSON_BYTES in sizes
    assert binding.identity_problem != IDENTITY_UNREADABLE
