"""Acceptance follow-up ledger step 3a (Issue #774 AC1c, AC1d, AC1e): Core results need a row."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from test_acceptance_outcomes import fx, inputs, verified  # noqa: F401

from experimental.acceptance_outcomes import OutcomesError, build_report, record_call
from experimental.acceptance_outcomes.cli import main
from experimental.acceptance_outcomes.report import format_report

CORE = [
    {"status": "FAILED", "reason": "x"},
    {"status": "UNCOMPARABLE", "reason": "x", "core_status": "FAILED"},
    {"status": "REFUSED", "reason": "x", "core_status": "REFUSED"},
]
PRE_CORE = [{"status": "REFUSED", "reason": "x"}, {"error": "BAD_SCOPE"}]


@pytest.mark.parametrize("res", [verified({}), *CORE], ids=lambda r: r["status"])
def test_ac1c_core_result_without_notary_row_refused(fx: dict[str, Any], res: Any) -> None:  # noqa: F811
    for db in (None, fx["db"]):  # no database given, or a database with no such row
        item = {**res, "task_id": "t", "attempt_id": "none"} if db else res
        with pytest.raises(OutcomesError, match="OUTCOME_RECORD_MISMATCH"):
            record_call(fx["led"], item, inputs(fx), db)
    assert not fx["led"].exists()


def test_ac1c_pre_core_stays_no_notary_row(fx: dict[str, Any]) -> None:  # noqa: F811
    for res in PRE_CORE:
        assert record_call(fx["led"], res, inputs(fx, candidate="5" * 40)) == "ADDED"
    rows = [json.loads(x) for x in fx["led"].read_text().splitlines()]
    assert [r["no_notary_row"] for r in rows] == [True, True]
    assert build_report(fx["led"])["v1"]["reached_core"] == 0


def test_ac1d_lost_flag_marks_line_and_report(fx: dict[str, Any]) -> None:  # noqa: F811
    led = fx["led"]
    for i, res in enumerate([verified({}), *CORE]):
        cand = inputs(fx, candidate=str(i) * 40)
        assert record_call(led, res, cand, None, notary_lost="state gone") == "ADDED"
    rows = [json.loads(x) for x in led.read_text().splitlines()]
    assert all(r["notary_lost"] is True and r["notary_lost_reason"] == "state gone" for r in rows)
    assert all(r["no_notary_row"] is False for r in rows)
    rep = build_report(led)["v1"]
    assert rep["reached_core"] == 4 and rep["unchecked_binding"] == 4
    text = format_report({"v1": rep})
    rate_lines = [x for x in text.splitlines() if "overturn rate" in x or "FAILED/REFUSED" in x]
    assert len(rate_lines) == 2 and all("unchecked binding 4" in x for x in rate_lines)


def test_ac1d_lost_flag_negatives(fx: dict[str, Any]) -> None:  # noqa: F811
    cases = [(res, None, "gone") for res in PRE_CORE]  # pre-Core result
    cases += [(verified({}), fx["db"], "gone"), (verified({}), None, "")]  # db given; no reason
    for res, db, why in cases:
        with pytest.raises(OutcomesError, match="OUTCOME_"):
            record_call(fx["led"], res, inputs(fx), db, notary_lost=why)
    assert not fx["led"].exists()


def test_ac1d_cli_flag(fx: dict[str, Any], tmp_path: Path) -> None:  # noqa: F811
    res = tmp_path / "r.json"
    res.write_text(json.dumps(verified({})))
    rec = ["record", "--ledger", str(fx["led"]), "--result", str(res), "--repo", "r"]
    rec += ["--base", fx["base"], "--candidate", fx["cand"], "--version", "v1"]
    assert main(rec) == 2
    assert main([*rec, "--notary-lost", "cleaned up"]) == 0
    assert json.loads(fx["led"].read_text())["notary_lost_reason"] == "cleaned up"


def test_ac1e_old_ledger_format_unchanged(fx: dict[str, Any]) -> None:  # noqa: F811
    assert record_call(fx["led"], verified({}), inputs(fx), fx["db"]) == "ADDED"
    assert record_call(fx["led"], PRE_CORE[0], inputs(fx, candidate="5" * 40)) == "ADDED"
    for line in fx["led"].read_text().splitlines():
        assert "notary_lost" not in json.loads(line)
    first = json.loads(fx["led"].read_text().splitlines()[0])
    assert sorted(first) == sorted(
        [
            "type",
            "repo",
            "base",
            "candidate",
            "version",
            "test_commit",
            "verdict",
            "reason",
            "task_id",
            "attempt_id",
            "no_notary_row",
            "call_id",
            "prev",
        ]  # fmt: skip
    )
    rep = build_report(fx["led"])["v1"]
    assert rep["total"] == 2 and rep["reached_core"] == 1 and rep["unchecked_binding"] == 0
