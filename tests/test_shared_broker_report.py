"""The shared honest #1353 report is read back only when its hash line matches (#1492)."""

from __future__ import annotations

import hashlib
import json

from shared_broker_report import _read


def _store(path, body: bytes, digest: str | None = None) -> None:
    path.write_bytes((digest or hashlib.sha256(body).hexdigest()).encode() + b"\n" + body)


def test_a_stored_report_with_a_matching_hash_is_read_back(tmp_path):
    _store(tmp_path / "r.json", json.dumps({"rows": [1]}).encode())
    assert _read(tmp_path / "r.json") == {"rows": [1]}


def test_a_stored_report_whose_body_changed_is_refused(tmp_path):
    body = json.dumps({"rows": [1]}).encode()
    _store(tmp_path / "r.json", body.replace(b"1", b"2"), hashlib.sha256(body).hexdigest())
    assert _read(tmp_path / "r.json") is None


def test_garbage_a_missing_file_and_a_report_without_rows_are_refused(tmp_path):
    (tmp_path / "g.json").write_bytes(b"garbage")
    _store(tmp_path / "n.json", json.dumps({"other": 1}).encode())
    assert [_read(tmp_path / n) for n in ("g.json", "n.json", "missing.json")] == [None] * 3
