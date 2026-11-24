"""#1578: pin the stored-JSON size cap of ``notary_binding.row_binding`` (exactly the cap is read,
one byte more is withheld, bytes not characters, the cap value itself)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from attest.test_attest_cli import CORE_IDENTITY, fake_core_verify
from conftest import commit_change, git

from cairnwork import notary_binding
from cairnwork.core_request import AcceptanceSpec, VerifierObservation
from cairnwork.notary import Notary

CAP = 4 * 1024 * 1024  # written out here on purpose: changing the source constant must fail


def _notary(tmp_path: Path, repo: Path) -> tuple[Notary, Path]:
    db = tmp_path / "notary.db"
    notary = Notary(db, core_verify=fake_core_verify, core_identity=CORE_IDENTITY)
    base = git(repo, "rev-parse", "HEAD")
    cand = commit_change(repo, {"src/b1578.py": "x = 1\n"})
    spec = AcceptanceSpec("task-1", "sha256:" + "c" * 64, ("pytest-0",), ("src/b1578.py",), False)
    obs = (VerifierObservation("pytest-0", "artifact-1", "sha256:" + "e" * 64, True),)
    notary.notarize("task-1", "a1", spec, repo, base, cand, obs)
    return notary, db


def _set(db: Path, column: str, value: str) -> None:
    conn = sqlite3.connect(db)
    try:
        conn.execute("DROP TRIGGER IF EXISTS core_verifications_no_update")
        conn.execute(f"UPDATE core_verifications SET {column} = ?", (value,))  # noqa: S608
        conn.commit()
    finally:
        conn.close()


def _padded(total_bytes: int, filler: str = "x") -> str:
    head, tail = '{"pad":"', '"}'
    room = total_bytes - len(head) - len(tail)
    assert room % len(filler.encode()) == 0
    return head + filler * (room // len(filler.encode())) + tail


def _sizes(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    sizes: list[int] = []
    real = json.loads

    def spy(value: Any, *args: Any, **kwargs: Any) -> Any:
        sizes.append(len(value.encode()))
        return real(value, *args, **kwargs)

    monkeypatch.setattr(json, "loads", spy)
    return sizes


COLUMNS = ["request_json", "response_json", "binding_json"]


def test_the_cap_constant_is_four_mebibytes() -> None:
    assert notary_binding.MAX_STORED_JSON_BYTES == CAP


@pytest.mark.parametrize("column", COLUMNS)
def test_a_value_of_exactly_the_cap_is_parsed(
    tmp_path: Path, repo: Path, monkeypatch: pytest.MonkeyPatch, column: str
) -> None:
    notary, db = _notary(tmp_path, repo)
    _set(db, column, _padded(CAP))
    sizes = _sizes(monkeypatch)
    notary_binding.row_binding(notary, "task-1", "a1")
    assert CAP in sizes


@pytest.mark.parametrize("column", COLUMNS)
@pytest.mark.parametrize("filler,extra", [("x", 1), ("é", 2)], ids=["one-byte-over", "bytes"])
def test_a_value_over_the_cap_is_never_parsed_and_reads_unreadable(
    tmp_path: Path,
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    column: str,
    filler: str,
    extra: int,
) -> None:
    notary, db = _notary(tmp_path, repo)
    text = _padded(CAP + extra, filler)
    assert extra == 1 or len(text) <= CAP  # the multi-byte case is under the cap in characters
    _set(db, column, text)
    sizes = _sizes(monkeypatch)
    binding = notary_binding.row_binding(notary, "task-1", "a1")
    assert binding is not None
    assert binding.identity_problem == notary_binding.IDENTITY_UNREADABLE
    assert binding.columns_match is False
    assert len(text.encode()) not in sizes
