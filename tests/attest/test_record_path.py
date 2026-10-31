"""read_record opens the notary database at the exact path, read-only."""

from __future__ import annotations

from pathlib import Path

import pytest
from test_record import setup_sample_notary

from cairnwork_attest.record import RecordNotFound, read_record


@pytest.mark.parametrize("dirname", ["cw#1", "cw?x=1", "cw%41", "cw 1"])
def test_read_record_opens_the_exact_path_with_special_characters(
    tmp_path: Path, repo: Path, dirname: str
) -> None:
    state = tmp_path / dirname
    state.mkdir()
    db_path = state / "notary.sqlite"
    _, verdict = setup_sample_notary(db_path, repo, "task-1", "a1")

    record = read_record(db_path, "task-1", "a1")
    assert record.request_hash == verdict.request_hash
    # nothing was created at a truncated or decoded sibling path
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted([dirname, "repo"])


@pytest.mark.parametrize("dirname", ["cw#1", "cw?x=1", "cw%41"])
def test_read_record_missing_file_creates_nothing(tmp_path: Path, dirname: str) -> None:
    state = tmp_path / dirname
    state.mkdir()
    before = sorted(p.name for p in tmp_path.iterdir())
    with pytest.raises(RecordNotFound) as excinfo:
        read_record(state / "notary.sqlite", "task-1", "a1")
    assert excinfo.value.code == "RECORD_NOT_FOUND"
    assert sorted(p.name for p in tmp_path.iterdir()) == before
    assert list(state.iterdir()) == []
