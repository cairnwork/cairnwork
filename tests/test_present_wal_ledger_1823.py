"""#1823: two owner-state presence checks that read "cannot look" as "not there": the notary's
write-ahead-log beside the file, and the ownership ledger in ``Intake.in_flight``. Only "not
found" means absent; any other OS error refuses (BAD_STATE). The lookup is made to fail with
PermissionError by monkeypatch, so root and the interpreter do not matter."""

import os
import sqlite3

import pytest

from cairnwork import notary_migrate
from cairnwork.intake import Intake
from cairnwork.intake_request import IntakeRefused
from cairnwork.notary import NotarySchemaMismatch


def _deny(monkeypatch, name):
    for attr in ("stat", "lstat"):
        real = getattr(os, attr)

        def guarded(path, *args, _real=real, **kwargs):
            if os.path.basename(os.fspath(path)) == name:
                raise PermissionError(13, "Permission denied")
            return _real(path, *args, **kwargs)

        monkeypatch.setattr(os, attr, guarded)


def _notary(tmp_path):
    path = tmp_path / "notary.sqlite"
    db = sqlite3.connect(path)
    db.execute("create table t(x)")
    db.commit()
    db.close()
    return path


def test_an_unreadable_wal_entry_refuses_instead_of_reading_as_no_wal(tmp_path, monkeypatch):
    path = _notary(tmp_path)
    _deny(monkeypatch, "notary.sqlite-wal")
    with pytest.raises(IntakeRefused) as refused:
        notary_migrate._probe(path)
    assert refused.value.code == "BAD_STATE"


def test_a_missing_wal_still_passes_and_a_real_wal_still_mismatches(tmp_path):
    path = _notary(tmp_path)
    assert notary_migrate._probe(path) is not None  # no -wal: as before
    path.with_name(path.name + "-wal").write_bytes(b"x")
    with pytest.raises(NotarySchemaMismatch):
        notary_migrate._probe(path)


def test_an_unreadable_ledger_entry_is_not_nothing_in_flight(tmp_path, monkeypatch):
    intake = Intake(tmp_path / "state")
    intake.prepare()
    intake.ledger.in_flight()  # creates the ledger
    _deny(monkeypatch, "ownership.sqlite")
    with pytest.raises(IntakeRefused) as refused:
        intake.in_flight()
    assert refused.value.code == "BAD_STATE"


def test_a_missing_ledger_still_means_nothing_in_flight(tmp_path):
    intake = Intake(tmp_path / "state")
    intake.prepare()
    assert intake.in_flight()["count"] == 0
