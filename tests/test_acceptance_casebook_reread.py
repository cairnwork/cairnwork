"""#936: a case line is re-checked on read by the rules ``add`` enforced (kind vs origin,
candidate/base vs the row), so a hand-appended line, even one adopted by ``repair``, is never
eligible."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_acceptance_casebook_origin import _add, _book, _list, _no_harness_session, lg  # noqa: F401

from experimental.acceptance_casebook.repair import repair
from experimental.acceptance_casebook.store import _append, _read_lines, eligible_cases

BAD = {
    "kind_vs_origin": {"kind": "refused_good"},
    "candidate_vs_row": {"candidate": "f" * 40},
    "base_vs_row": {"base": "e" * 40},
}


def _forge(lg: dict[str, str], change: dict[str, str], via_repair: bool) -> None:  # noqa: F811
    book = _book(lg)
    entries, prev = _read_lines(book)
    entry = {k: v for k, v in entries[0][0].items() if k != "prev"} | change
    if via_repair:  # appended by hand: the head stays one line behind, then repair adopts it
        line = json.dumps({**entry, "prev": prev}, sort_keys=True) + "\n"
        with book.open("a") as handle:
            handle.write(line)
        assert repair(book) == "REPAIRED"
    else:
        _append(book, entry, prev)


@pytest.mark.parametrize("via_repair", [False, True])
@pytest.mark.parametrize("name", sorted(BAD))
def test_forged_case_line_is_never_eligible(lg, capsys, name, via_repair) -> None:  # noqa: F811
    assert _add(lg, lg["cand"], "a1", "accepted_bad") == 0
    _forge(lg, BAD[name], via_repair)
    states = [c["state"] for c in _list(lg, capsys, "--notary-db", lg["db"])]
    assert states[0] != "origin_changed" and states[1] == "origin_changed"
    eligible = eligible_cases(_book(lg), Path(lg["db"]))
    assert all(c["line_sha256"] != _read_lines(_book(lg))[0][1][1] for c in eligible)


@pytest.mark.parametrize("via_repair", [False, True])
@pytest.mark.parametrize("kind", ["banana", "", "ACCEPTED_BAD", "refused", None, 1])
def test_unknown_kind_on_a_refused_origin_is_never_eligible(lg, capsys, kind, via_repair) -> None:  # noqa: F811
    assert _add(lg, lg["refused"], "a2", "refused_good") == 0
    _forge(lg, {"kind": kind}, via_repair)  # type: ignore[dict-item]
    states = [c["state"] for c in _list(lg, capsys, "--notary-db", lg["db"])]
    assert states[0] != "origin_changed" and states[1] == "origin_changed"


def test_honest_case_still_holds(lg, capsys) -> None:  # noqa: F811
    assert _add(lg, lg["cand"], "a1", "accepted_bad") == 0
    assert _add(lg, lg["refused"], "a2", "refused_good") == 0
    states = [c["state"] for c in _list(lg, capsys, "--notary-db", lg["db"])]
    assert states == ["unconfirmed", "unconfirmed"]
