"""#935: row_binding under memory pressure and close() failures (split from the main test file)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from attest.test_attest_cli import fake_core_verify
from test_notary_row_binding_935 import _row, _sql  # noqa: F401


def test_memory_error_while_judging_is_unreadable(tmp_path: Path, repo: Path, monkeypatch) -> None:
    import cairnwork.notary_binding as nb

    notary, _ = _row(tmp_path, repo, fake_core_verify)

    def boom(*_a, **_k):
        raise MemoryError

    monkeypatch.setattr(nb, "_columns_match", boom)
    got = notary.row_binding("task-1", "a1")
    assert got is not None and got.identity_problem == nb.IDENTITY_UNREADABLE
    monkeypatch.setattr(nb, "_identity_digest", boom)  # even the digest of the identity may fail
    got = notary.row_binding("task-1", "a1")
    assert got is not None and got.identity_problem == nb.IDENTITY_UNREADABLE
    assert got.expected_identity_sha256 is None


def test_oversized_binding_json_is_unreadable(tmp_path: Path, repo: Path) -> None:
    import cairnwork.notary_binding as nb

    notary, db = _row(tmp_path, repo, fake_core_verify)
    _sql(
        db,
        "UPDATE core_verifications SET binding_json = ?",
        '{"pad": "' + "x" * (nb.MAX_STORED_JSON_BYTES + 1) + '"}',
    )
    got = notary.row_binding("task-1", "a1")
    assert got is not None and got.identity_problem == nb.IDENTITY_UNREADABLE
    assert got.columns_match is False


def test_memory_error_anywhere_in_the_read_is_unreadable(tmp_path: Path, repo: Path, monkeypatch):
    import cairnwork.notary_binding as nb

    notary, _ = _row(tmp_path, repo, fake_core_verify)

    def boom(*_a, **_k):
        raise MemoryError

    monkeypatch.setattr(nb.sqlite3, "connect", boom)  # the read itself runs out of memory
    got = notary.row_binding("task-1", "a1")
    assert got is not None and got.identity_problem == nb.IDENTITY_UNREADABLE
    assert got.columns_match is False and got.expected_identity_sha256 is None


def test_memory_error_in_execute_and_close_still_releases_the_connection(
    tmp_path: Path, repo: Path, monkeypatch
) -> None:
    import cairnwork.notary_binding as nb

    notary, _ = _row(tmp_path, repo, fake_core_verify)
    closes: list[int] = []

    class Conn:
        def execute(self, *_a, **_k):
            raise MemoryError

        def close(self) -> None:
            closes.append(1)
            if len(closes) == 1:
                raise MemoryError  # the first close fails; a second try must release it

    monkeypatch.setattr(nb.sqlite3, "connect", lambda *_a, **_k: Conn())
    got = notary.row_binding("task-1", "a1")
    assert got is not None and got.identity_problem == nb.IDENTITY_UNREADABLE
    assert got.columns_match is False
    assert len(closes) == 2, "close must be retried once after a MemoryError"


def test_close_error_after_a_memory_error_does_not_replace_it(
    tmp_path: Path, repo: Path, monkeypatch
) -> None:
    import cairnwork.notary_binding as nb

    notary, _ = _row(tmp_path, repo, fake_core_verify)

    class Conn:
        def execute(self, *_a, **_k):
            raise MemoryError

        def close(self) -> None:
            raise sqlite3.OperationalError("cannot close")  # must not mask the MemoryError

    monkeypatch.setattr(nb.sqlite3, "connect", lambda *_a, **_k: Conn())
    got = notary.row_binding("task-1", "a1")  # MemoryError wins: unreadable, not Unavailable
    assert got is not None and got.identity_problem == nb.IDENTITY_UNREADABLE


class _RealConnWithBadClose:
    """A real read-only connection whose close() fails ``failures`` times, then works."""

    def __init__(self, real, failures: int, error: Exception) -> None:
        self.real, self.failures, self.error, self.closes = real, failures, error, 0

    def execute(self, *a, **k):
        return self.real.execute(*a, **k)

    def close(self) -> None:
        self.closes += 1
        if self.closes <= self.failures:
            raise self.error
        self.real.close()


def _patch_connect(monkeypatch, failures: int, error: Exception) -> list:
    import cairnwork.notary_binding as nb

    made: list = []
    real_connect = nb.sqlite3.connect

    def connect(*a, **k):
        conn = _RealConnWithBadClose(real_connect(*a, **k), failures, error)
        made.append(conn)
        return conn

    monkeypatch.setattr(nb.sqlite3, "connect", connect)
    return made


def test_close_error_is_reported_even_inside_a_callers_except_block(
    tmp_path: Path, repo: Path, monkeypatch
) -> None:
    import cairnwork.notary_binding as nb

    notary, _ = _row(tmp_path, repo, fake_core_verify)
    _patch_connect(monkeypatch, 5, sqlite3.OperationalError("cannot close"))
    try:
        raise ValueError("the caller is handling this")
    except ValueError:
        with pytest.raises(nb.RowBindingUnavailable):  # not swallowed into a None / success
            notary.row_binding("task-1", "a1")


def test_first_close_error_second_succeeds_returns_the_binding(
    tmp_path: Path, repo: Path, monkeypatch
) -> None:
    notary, _ = _row(tmp_path, repo, fake_core_verify)
    made = _patch_connect(monkeypatch, 1, sqlite3.OperationalError("cannot close"))
    got = notary.row_binding("task-1", "a1")
    assert got is not None and got.identity_problem == "" and got.columns_match is True
    assert made[0].closes == 2
