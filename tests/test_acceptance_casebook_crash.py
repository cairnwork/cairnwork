"""Acceptance casebook #772 P1-A: the known limit of the protection, and a crash between the
append and the head update (refused with no partial output; recovery is explicit and recorded)."""

from __future__ import annotations

import hashlib
import json
import os

import pytest
from test_acceptance_casebook_origin import _add, _book, _list, _no_harness_session, lg  # noqa: F401

from experimental.acceptance_casebook.cli import main


def test_known_limit_rewriting_file_and_head_together_is_not_detected(lg, capsys) -> None:  # noqa: F811
    """States the limit (#763 docstring): the chain and head catch accidental edits, not a
    deliberate rewrite of both. Signatures or a remote ledger would be needed (out of scope)."""
    for v in ("v1", "v2"):
        assert _add(lg, lg["cand"], "a1", "accepted_bad", version=v) == 0
    first = _book(lg).read_bytes().splitlines(keepends=True)[0]
    _book(lg).write_bytes(first)  # drop v2, then write a head that agrees
    _book(lg).with_name(_book(lg).name + ".head").write_text(
        hashlib.sha256(first).hexdigest() + "\n"
    )
    assert [c["version"] for c in _list(lg, capsys)] == ["v1"]  # accepted: not detected


def _crash_on_head_update(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args: object) -> None:
        raise OSError("crash before the head moved")

    monkeypatch.setattr(os, "replace", boom)


def test_crash_after_append_refused_then_repair_recorded(lg, capsys, monkeypatch) -> None:  # noqa: F811
    assert _add(lg, lg["cand"], "a1", "accepted_bad", version="v1") == 0
    with monkeypatch.context() as m:
        _crash_on_head_update(m)
        with pytest.raises(OSError):
            _add(lg, lg["cand"], "a1", "accepted_bad", version="v2")
    assert len(_book(lg).read_text().splitlines()) == 2  # appended, head still on v1
    capsys.readouterr()
    assert main(["list", "--casebook", str(_book(lg))]) == 2
    out = capsys.readouterr().out
    assert "CASEBOOK_NOT_APPEND_ONLY" in out and "v1" not in out and "v2" not in out
    assert _add(lg, lg["cand"], "a1", "accepted_bad", version="v3") == 2  # no add on top
    stale = _book(lg).with_name(_book(lg).name + ".head").read_text().strip()

    assert main(["repair", "--casebook", str(_book(lg))]) == 0
    assert "REPAIRED" in capsys.readouterr().out
    last = json.loads(_book(lg).read_text().splitlines()[-1])
    assert last["type"] == "repair" and last["stale_head"] == stale
    v2 = _book(lg).read_bytes().splitlines(keepends=True)[1]
    assert last["adopted_head"] == hashlib.sha256(v2).hexdigest()
    assert [c["version"] for c in _list(lg, capsys)] == ["v1", "v2"]  # repair line is no case
    assert main(["repair", "--casebook", str(_book(lg))]) == 0
    assert "CLEAN" in capsys.readouterr().out


@pytest.mark.parametrize("damage", ["edit", "two_behind", "partial_line"])
def test_repair_refuses_anything_but_one_lost_head_update(lg, capsys, damage: str) -> None:  # noqa: F811
    for v in ("v1", "v2", "v3"):
        assert _add(lg, lg["cand"], "a1", "accepted_bad", version=v) == 0
    lines = _book(lg).read_bytes().splitlines(keepends=True)
    head = _book(lg).with_name(_book(lg).name + ".head")
    if damage == "edit":
        _book(lg).write_bytes(lines[0] + lines[1].replace(b"reviewer-x", b"someone") + lines[2])
    elif damage == "two_behind":
        head.write_text(hashlib.sha256(lines[0]).hexdigest() + "\n")
    else:
        _book(lg).write_bytes(b"".join(lines) + b'{"half": ')
    before = (_book(lg).read_bytes(), head.read_text())
    capsys.readouterr()
    assert main(["repair", "--casebook", str(_book(lg))]) == 2
    assert "CASEBOOK_NOT_APPEND_ONLY" in capsys.readouterr().out
    assert (_book(lg).read_bytes(), head.read_text()) == before
