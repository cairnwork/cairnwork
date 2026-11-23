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


def _padded(total: int, filler: str = "x") -> str:
    head, tail = '{"pad":"', '"}'
    width = len(filler.encode())
    room = total - len(head) - len(tail)
    assert room % width == 0
    return head + filler * (room // width) + tail


def _spy(monkeypatch) -> list[int]:
    parsed: list[int] = []
    real = record_module.json.loads
    monkeypatch.setattr(
        record_module.json,
        "loads",
        lambda value, *a, **k: (parsed.append(len(value.encode())), real(value, *a, **k))[1],
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


@pytest.mark.parametrize("column", ["request_json", "response_json", "binding_json"])
def test_a_stored_json_of_exactly_the_cap_is_still_read(ledger, monkeypatch, column):
    state, task, attempt = ledger
    parsed = _spy(monkeypatch)
    _rewrite(state, column, _padded(MAX_STORED_JSON_BYTES))
    try:
        read_record(state / "notary.sqlite", task, attempt)
    except RecordMalformed as err:
        assert err.code != "RECORD_TOO_LARGE"
    assert MAX_STORED_JSON_BYTES in parsed


def test_an_oversized_stored_json_is_never_loaded_into_python(ledger):
    state, task, attempt = ledger
    _rewrite(state, "request_json", _padded(MAX_STORED_JSON_BYTES + 1))
    row = record_module._fetch_row(  # noqa: SLF001
        f"{(state / 'notary.sqlite').resolve().as_uri()}?mode=ro", task, attempt
    )
    assert row[5] is None and row[-1]  # request text withheld, too-large flag set


def test_a_normal_row_still_reads(ledger):
    state, task, attempt = ledger
    rec = read_record(state / "notary.sqlite", task, attempt)
    assert json.loads(rec.request_json) == rec.request


@pytest.mark.parametrize("column", ["request_json", "response_json", "binding_json"])
def test_the_cap_counts_bytes_not_characters(ledger, monkeypatch, column):
    state, task, attempt = ledger
    parsed = _spy(monkeypatch)
    text = _padded(MAX_STORED_JSON_BYTES + 2, "é")  # 2 bytes each: under the cap in characters
    assert len(text) <= MAX_STORED_JSON_BYTES < len(text.encode())
    _rewrite(state, column, text)
    with pytest.raises(RecordMalformed) as err:
        read_record(state / "notary.sqlite", task, attempt)
    assert err.value.code == "RECORD_TOO_LARGE"
    assert all(size <= MAX_STORED_JSON_BYTES for size in parsed)


def _schema2_ledger(path: Path, request_json: str) -> Path:
    db = path / "notary.sqlite"
    conn = sqlite3.connect(db)
    try:  # a ledger not yet migrated to schema 3: no binding_json column
        conn.execute(
            "CREATE TABLE core_verifications (task_id, attempt_id, base_commit, candidate_commit,"
            " request_hash, request_json, http_status, status, integrity, response_json,"
            " recorded_at, expected_identity)"
        )
        conn.execute(
            "INSERT INTO core_verifications VALUES ('t','a','b','c','h',?,200,'VERIFIED','i',"
            "'{\"r\":1}','now',NULL)",
            (request_json,),
        )
        conn.commit()
    finally:
        conn.close()
    return db


def test_a_schema_2_ledger_without_binding_json_still_reads(tmp_path):
    rec = read_record(_schema2_ledger(tmp_path, '{"q":1}'), "t", "a")
    assert rec.binding is None and rec.request == {"q": 1}


def test_a_schema_2_ledger_with_an_oversized_request_is_refused(tmp_path):
    db = _schema2_ledger(tmp_path, _padded(MAX_STORED_JSON_BYTES + 1))
    with pytest.raises(RecordMalformed) as err:
        read_record(db, "t", "a")
    assert err.value.code == "RECORD_TOO_LARGE"
