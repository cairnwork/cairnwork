"""Issue #809 protects one-call ownership and rate eligibility."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

import pytest
from test_acceptance_outcomes import confirm, finding, fx, inputs, verified  # noqa: F401, F811

from experimental.acceptance_outcomes import OutcomesError, add_event, build_report, record_call
from experimental.acceptance_outcomes.report import format_report


def test_ac1_notary_row_allows_only_one_call(fx: dict[str, Any]) -> None:  # noqa: F811
    result = verified(fx)
    assert record_call(fx["led"], result, inputs(fx), fx["db"]) == "ADDED"
    assert record_call(fx["led"], result, inputs(fx), fx["db"]) == "DUPLICATE"
    with pytest.raises(OutcomesError) as exc:
        record_call(
            fx["led"], {**result, "reason": "different"}, inputs(fx, call_ref="second"), fx["db"]
        )
    assert exc.value.code == "OUTCOME_DUPLICATE_ROW"
    pre_core = {"error": "BAD_SCOPE"}
    assert record_call(fx["led"], pre_core, inputs(fx, candidate="a" * 40)) == "ADDED"


def test_ac3_verified_raw_status_must_verify_notary_verdict(
    fx: dict[str, Any],  # noqa: F811
) -> None:
    with sqlite3.connect(fx["db"]) as db:
        db.execute("DROP TRIGGER IF EXISTS core_verifications_no_update")
        db.execute("UPDATE core_verifications SET integrity = 'INVALID'")
    with pytest.raises(OutcomesError) as exc:
        record_call(fx["led"], verified(fx), inputs(fx), fx["db"])
    assert exc.value.code == "OUTCOME_RECORD_MISMATCH"
    assert "notary verdict: RECORD_NOT_VERIFIED" in str(exc.value)


def test_ac3_genuine_verified_row_is_added(fx: dict[str, Any]) -> None:  # noqa: F811
    assert record_call(fx["led"], verified(fx), inputs(fx), fx["db"]) == "ADDED"


def test_ac2_notary_lost_cases_do_not_enter_any_rate(fx: dict[str, Any]) -> None:  # noqa: F811
    led = fx["led"]
    lost_ver = verified(fx)
    record_call(led, lost_ver, inputs(fx), notary_lost="gone")
    lost_ver_id = json.loads(led.read_text().splitlines()[0])["call_id"]
    record_call(led, verified(fx), inputs(fx), fx["db"])
    bound_ver_id = json.loads(led.read_text().splitlines()[1])["call_id"]
    for cid in (lost_ver_id, bound_ver_id):
        add_event(led, cid, {"type": "observed", "start": "a", "end": "b"})
        add_event(led, cid, {"type": "closed", "checks_done": True})
    record_call(led, {"status": "FAILED", "reason": "lost"}, inputs(fx), notary_lost="gone")
    record_call(
        led,
        {"status": "REFUSED", "core_status": "REFUSED", "reason": "lost"},
        inputs(fx),
        notary_lost="gone",
    )
    lost_refused_id = json.loads(led.read_text().splitlines()[-1])["call_id"]
    record_call(led, {"status": "REFUSED", "reason": "bound"}, inputs(fx, candidate="b" * 40))
    bound_refused_id = json.loads(led.read_text().splitlines()[-1])["call_id"]
    finding(fx, lost_refused_id)
    confirm(fx, lost_refused_id, outcome="refused_but_correct")
    finding(fx, bound_refused_id)
    confirm(fx, bound_refused_id, fid="f2", outcome="refused_but_correct")
    rep = build_report(led)["v1"]
    assert (rep["VERIFIED"]["num"], rep["VERIFIED"]["den"], rep["VERIFIED"]["coverage"]) == (
        0,
        1,
        "1/1",
    )
    r = rep["refused_but_correct"]
    assert (r["num"], r["den"], r["coverage"]) == (1, 1, "1/1")
    assert rep["unchecked_binding"] == 3
    assert rep["cases"]["notary_lost"]["VERIFIED"] == [lost_ver_id]
    assert "notary-lost outside rate" in format_report({"v1": rep})


def test_ac2_notary_lost_excluded_with_closed_cases(fx: dict[str, Any]) -> None:  # noqa: F811
    led = fx["led"]
    result = {"status": "FAILED", "reason": "lost"}
    record_call(led, result, inputs(fx), notary_lost="gone")
    lost_id = json.loads(led.read_text().splitlines()[0])["call_id"]
    result2 = {"status": "REFUSED", "reason": "bound"}
    record_call(led, result2, inputs(fx, candidate="b" * 40))
    bound_id = json.loads(led.read_text().splitlines()[1])["call_id"]
    finding(fx, bound_id)
    confirm(fx, bound_id, outcome="refused_but_correct")
    report = build_report(led)["v1"]
    assert report["cases"]["notary_lost"]["FAILED"] == [lost_id]
