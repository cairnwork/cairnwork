"""Acceptance casebook #772 AC1b: a case is confirmed only by a confirmer independent of its adder
(other session, outside its lineage, other model family or a human, with a source record and the
check's evidence). Each refusal is also re-checked on read."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_acceptance_casebook_origin import _add, _book, _ev, _list, _no_harness_session, lg  # noqa: F401

from experimental.acceptance_casebook.cli import main

ENV = "CLAUDE_CODE_SESSION_ID"


@pytest.fixture
def case(lg: dict[str, str], monkeypatch: pytest.MonkeyPatch, capsys) -> str:  # noqa: F811
    monkeypatch.setenv(ENV, "sess-adder")  # the adder is a Claude Code session, lineage root
    assert _add(lg, lg["cand"], "a1", "accepted_bad") == 0
    monkeypatch.delenv(ENV)
    (only,) = _list(lg, capsys)
    assert only["adder"]["session_id"] == "sess-adder"
    return only["line_sha256"]


def _confirm(lg: dict[str, str], digest: str, *identity: str) -> int:  # noqa: F811
    return main(["confirm", "--casebook", str(_book(lg)), "--case", digest,
                 "--conclusion", "confirmed", "--check-method", "rerun on base",
                 "--check-evidence-file", _ev(lg, "check"), *identity])  # fmt: skip


def _refused(lg, digest, capsys, code: str, *identity: str) -> None:  # noqa: F811
    capsys.readouterr()
    lines = _book(lg).read_bytes()
    assert _confirm(lg, digest, *identity) == 2
    assert code in capsys.readouterr().out
    assert _book(lg).read_bytes() == lines  # nothing appended
    assert _list(lg, capsys)[0]["state"] == "unconfirmed"


def test_1_adder_confirming_its_own_case_refused(lg, case, capsys, monkeypatch) -> None:  # noqa: F811
    monkeypatch.setenv(ENV, "sess-adder")
    _refused(lg, case, capsys, "SAME_SESSION", "--lineage", "root")


def test_2_same_session_other_display_name_refused(lg, case, capsys, monkeypatch) -> None:  # noqa: F811
    monkeypatch.setenv(ENV, "sess-adder")
    _refused(lg, case, capsys, "SAME_SESSION", "--lineage", "root", "--display-name", "other")


def test_3_copy_of_adder_session_refused(lg, case, capsys, monkeypatch) -> None:  # noqa: F811
    monkeypatch.setenv(ENV, "sess-copy")  # forked from the adder: the adder is in its lineage
    _refused(lg, case, capsys, "SAME_LINEAGE", "--lineage", "coord-root,sess-adder")


def test_4_same_family_other_session_not_confirmed(lg, case, capsys, monkeypatch) -> None:  # noqa: F811
    monkeypatch.setenv(ENV, "sess-other")
    _refused(lg, case, capsys, "SAME_FAMILY", "--lineage", "root")


@pytest.mark.parametrize("who", ["other_family", "human"])
def test_5_other_family_or_human_with_source_confirms(lg, case, capsys, who: str) -> None:  # noqa: F811
    src = _ev(lg, "source")
    if who == "human":
        ident = ["--github-login", "james-reviewer", "--source-ref",
                 "https://github.com/cairnwork/cairnwork/issues/772#issuecomment-1"]  # fmt: skip
    else:
        ident = ["--dispatch-id", "agy-task-9", "--model-family", "google", "--lineage", "root"]
    assert _confirm(lg, case, *ident, "--source-file", src) == 0
    (listed,) = _list(lg, capsys)
    assert listed["state"] == "confirmed" and listed["type"] == "case"
    event = json.loads(_book(lg).read_text().splitlines()[-1])
    assert event["type"] == "confirmation" and event["case"] == case
    assert (
        event["check"]["method"] == "rerun on base" and len(event["check"]["evidence_sha256"]) == 64
    )
    assert event["confirmer"]["source_sha256"] and "candidate" not in event
    assert [c["version"] for c in _list(lg, capsys, "--notary-db", lg["db"], "--eligible")] == [
        "v1"
    ]


@pytest.mark.parametrize("missing", ["model_family", "lineage", "source"])
def test_6_missing_identity_part_stays_unconfirmed(lg, case, capsys, missing: str) -> None:  # noqa: F811
    ident = {"model_family": ["--model-family", "google"], "lineage": ["--lineage", "root"],
             "source": ["--source-file", _ev(lg, "source")]}  # fmt: skip
    flags = [f for k, v in ident.items() if k != missing for f in v]
    _refused(lg, case, capsys, "IDENTITY_INCOMPLETE", "--dispatch-id", "agy-task-9", *flags)


def test_6_adder_without_identity_cannot_be_confirmed(lg, capsys) -> None:  # noqa: F811
    assert _add(lg, lg["cand"], "a1", "accepted_bad", version="v9") == 0  # has an adder
    book = _book(lg)
    entry = json.loads(book.read_text())
    assert entry["adder"]["session_id"] == "adder-1"
    other = Path(lg["dir"]) / "bare.jsonl"
    rc = main(["add", "--casebook", str(other), "--notary-db", lg["db"], "--task-id", "task-1",
               "--attempt-id", "a1", "--candidate", lg["cand"], "--base", lg["base"],
               "--version", "v1", "--kind", "accepted_bad", "--found-by", "x",
               "--evidence-file", _ev(lg, "ev", candidate=lg["cand"], attempt_id="a1",
                                      kind="accepted_bad")])  # fmt: skip
    assert rc == 0 and "adder" not in json.loads(other.read_text())
    digest = __import__("hashlib").sha256(other.read_bytes()).hexdigest()
    capsys.readouterr()
    rc = main(["confirm", "--casebook", str(other), "--case", digest, "--conclusion", "confirmed",
               "--check-method", "m", "--check-evidence-file", _ev(lg, "c"),
               "--dispatch-id", "g", "--model-family", "google", "--lineage", "root",
               "--source-file", _ev(lg, "s")])  # fmt: skip
    assert rc == 2 and "IDENTITY_INCOMPLETE" in capsys.readouterr().out


def test_6_hand_written_confirmation_counts_only_if_it_passes_the_rule(lg, case, capsys) -> None:  # noqa: F811
    """Read side: a hand-written line is judged by the same rule. A line with every part filled
    and re-checkable does count: the chain and head stop edits, not appends (README)."""
    from experimental.acceptance_casebook.identity import resolve_actor
    from experimental.acceptance_casebook.store import _append, _read_chain

    url = "https://github.com/cairnwork/cairnwork/issues/772#issuecomment-9"
    who = resolve_actor({}, {"github_login": "h", "source_ref": url,
                             "source_file": _ev(lg, "h-body")})  # fmt: skip
    ev = str(Path(_ev(lg, "check")).resolve())
    good = {
        "method": "m",
        "evidence_ref": ev,
        "evidence_sha256": __import__("hashlib").sha256(Path(ev).read_bytes()).hexdigest(),
    }
    for bad in ({}, {**good, "evidence_sha256": "b" * 64},
                {**good, "who": {**who, "source_sha256": "a" * 64}},
                {**good, "who": {**who, "session_id": "H"}}):  # fmt: skip
        line = {"type": "confirmation", "case": case, "conclusion": "confirmed",
                "confirmer": bad.pop("who", who)}  # fmt: skip
        if bad:
            line["check"] = bad
        _append(_book(lg), line, _read_chain(_book(lg))[1])
        assert _list(lg, capsys)[0]["state"] == "unconfirmed"
    line = {"type": "confirmation", "case": case, "conclusion": "confirmed", "confirmer": who,
            "check": good}  # fmt: skip
    _append(_book(lg), line, _read_chain(_book(lg))[1])
    assert _list(lg, capsys)[0]["state"] == "confirmed"


def test_env_session_with_other_family_flag_refused(lg, case, capsys, monkeypatch) -> None:  # noqa: F811
    monkeypatch.setenv(ENV, "sess-x")
    _refused(lg, case, capsys, "IDENTITY_INVALID", "--lineage", "root", "--model-family", "google")
