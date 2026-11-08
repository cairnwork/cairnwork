"""Acceptance outcomes: close-after-observe ordering and PRE_CORE refused_but_correct."""

# ruff: noqa: F811, F401

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from attest.test_attest_cli import setup_cli_fixture
from conftest import git
from test_acceptance_outcomes import call_id, confirm, finding, fx, inputs, verified

from experimental.acceptance_outcomes import add_event, build_report, record_call


def _two_verified(fx: dict[str, Any], repo: Path) -> list[str]:
    record_call(fx["led"], verified(fx), inputs(fx), fx["db"])
    # #809: one notary row backs one call, so the second call gets its own row (attempt a2)
    (repo / "src" / "a.py").write_text("x = 3\n")  # so the fixture's change is a new commit
    git(repo, "commit", "-qam", "base 2")
    base2 = git(repo, "rev-parse", "HEAD")
    cand2 = setup_cli_fixture(fx["db"].parent, repo, attempt_id="a2")
    second = inputs(fx, base=base2, candidate=cand2)
    record_call(fx["led"], verified(fx, attempt_id="a2"), second, fx["db"])
    return [json.loads(x)["call_id"] for x in fx["led"].read_text().splitlines()]


def test_closed_before_observed_stays_unknown(fx: dict[str, Any], repo: Path) -> None:
    keep, late = _two_verified(fx, repo)
    add_event(fx["led"], keep, {"type": "observed", "start": "a", "end": "b"})
    add_event(fx["led"], keep, {"type": "closed", "checks_done": True})
    before = build_report(fx["led"])["v1"]["VERIFIED"]
    add_event(fx["led"], late, {"type": "closed", "checks_done": True})
    add_event(fx["led"], late, {"type": "observed", "start": "a", "end": "b"})
    b = build_report(fx["led"])["v1"]
    assert late in b["cases"]["unknown"] and late not in b["cases"]["no_defect_found"]
    after = b["VERIFIED"]
    assert after["den"] == before["den"] == 1 and after["unknown"] == 1
    assert after["coverage"] == before["coverage"] == "1/2"


def test_observed_before_closed_is_no_defect_found(fx: dict[str, Any]) -> None:
    cid = call_id(fx)
    add_event(fx["led"], cid, {"type": "observed", "start": "a", "end": "b"})
    add_event(fx["led"], cid, {"type": "closed", "checks_done": True})
    b = build_report(fx["led"])["v1"]
    assert b["cases"]["no_defect_found"] == [cid] and b["VERIFIED"]["coverage"] == "1/1"


def test_pre_core_refused_but_correct_is_its_own_block(fx: dict[str, Any]) -> None:
    record_call(fx["led"], {"status": "REFUSED", "reason": "p"}, inputs(fx))
    record_call(fx["led"], {"error": "BAD_SCOPE"}, inputs(fx, candidate="9" * 40))
    ids = [json.loads(x)["call_id"] for x in fx["led"].read_text().splitlines()]
    base = build_report(fx["led"])["v1"]["refused_but_correct"]
    pre = ids[1]
    finding(fx, pre)
    confirm(fx, pre, outcome="refused_but_correct")
    b = build_report(fx["led"])["v1"]
    p = b["pre_core_refused_but_correct"]
    assert (p["num"], p["den"], p["coverage"], p["cases"]) == (1, 1, "1/1", [pre])
    assert b["refused_but_correct"] == base


def test_two_closes_on_one_call_last_close_decides_and_counts_once(fx: dict[str, Any]) -> None:
    cid = call_id(fx)
    add_event(fx["led"], cid, {"type": "observed", "start": "a", "end": "b"})
    add_event(fx["led"], cid, {"type": "closed", "checks_done": True})
    assert build_report(fx["led"])["v1"]["cases"]["no_defect_found"] == [cid]
    assert add_event(fx["led"], cid, {"type": "closed", "checks_done": False}) == "ADDED"
    b = build_report(fx["led"])["v1"]
    assert b["cases"]["unknown"] == [cid] and b["cases"]["no_defect_found"] == []
    v = b["VERIFIED"]  # one call: counted once, in `unknown`, outside the rate denominator
    assert (v["tracked"], v["closed"], v["unknown"], v["den"], v["coverage"]) == (1, 0, 1, 0, "0/1")
    assert v["text"] == "unknown"
    add_event(fx["led"], cid, {"type": "closed", "checks_done": True})  # last close wins again
    b = build_report(fx["led"])["v1"]
    assert b["cases"]["no_defect_found"] == [cid] and b["VERIFIED"]["closed"] == 1
