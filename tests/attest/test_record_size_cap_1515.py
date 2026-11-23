"""#1515: the attest read-back of a notary row holds stored JSON to the notary's size cap."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

import cairnwork_attest.record as record_module
from cairnwork.notary_binding import MAX_STORED_JSON_BYTES
from cairnwork_attest.record import RecordMalformed, read_record
from tests.attest.binding_helpers import notarize_bound, producer


def _rewrite(state: Path, column: str, value: str) -> None:
    conn = sqlite3.connect(state / "notary.sqlite")
    try:
        conn.execute("DROP TRIGGER core_verifications_no_update")
        conn.execute(f"UPDATE core_verifications SET {column} = ?", (value,))  # noqa: S608 - test
        conn.commit()
    finally:
        conn.close()


def _padded(total: int) -> str:
    head, tail = '{"pad":"', '"}'
    return head + "x" * (total - len(head) - len(tail)) + tail


def _spy(monkeypatch) -> list[int]:
    parsed: list[int] = []
    real = record_module.json.loads
    monkeypatch.setattr(
        record_module.json,
        "loads",
        lambda value, *a, **k: (parsed.append(len(value)), real(value, *a, **k))[1],
    )
    return parsed


@pytest.fixture
def ledger(tmp_path):
    run = notarize_bound(tmp_path / "s", producer(tmp_path))
    return tmp_path / "s", run["task"], run["attempt"]


@pytest.mark.parametrize("column", ["request_json", "response_json", "binding_json"])
def test_an_oversized_stored_json_is_refused_before_parsing(ledger, monkeypatch, column):
    state, task, attempt = ledger
    parsed = _spy(monkeypatch)
    _rewrite(state, column, _padded(MAX_STORED_JSON_BYTES + 1))
    with pytest.raises(RecordMalformed) as err:
        read_record(state / "notary.sqlite", task, attempt)
    assert err.value.code == "RECORD_TOO_LARGE"
    assert all(size <= MAX_STORED_JSON_BYTES for size in parsed)


def test_a_stored_request_of_exactly_the_cap_is_still_read(ledger, monkeypatch):
    state, task, attempt = ledger
    parsed = _spy(monkeypatch)
    _rewrite(state, "request_json", _padded(MAX_STORED_JSON_BYTES))
    try:
        read_record(state / "notary.sqlite", task, attempt)
    except RecordMalformed as err:
        assert err.code != "RECORD_TOO_LARGE"
    assert MAX_STORED_JSON_BYTES in parsed


def test_a_normal_row_still_reads(ledger):
    state, task, attempt = ledger
    rec = read_record(state / "notary.sqlite", task, attempt)
    assert json.loads(rec.request_json) == rec.request
