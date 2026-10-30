"""PAUSE (#476) AC5: one receipt per decision, bound to its plan and to the event order."""

import hashlib
import json

import pytest

from experimental.pause import receipt
from experimental.pause.schema import RECEIPT_FIELDS, RECEIPT_SCHEMA, Reason

PLAN = "1. read src/app.py\n2. change x to 2\n"


def _intervened(**over):
    fields = dict(
        task_id="TASK_01",
        episode_id="ep_x",
        checkpoint_sha256="c" * 64,
        cut_turn_index=3,
        plan=PLAN,
        planner_model_id="planner-model",
        planner_effort="low",
        gate_model_id="gate-model",
        gate_effort="low",
        gate_verdict="ACCEPT",
        intervened=True,
        event_seq=7,
    )
    return receipt.build(**{**fields, **over})


def test_an_intervention_receipt_has_every_field_and_verifies():
    rec = _intervened()
    assert list(rec) == list(RECEIPT_FIELDS)
    assert rec["schema"] == RECEIPT_SCHEMA and rec["reason"] is None
    assert rec["plan_sha256"] == hashlib.sha256(PLAN.encode()).hexdigest()  # as local_worker's
    assert receipt.verify(rec, PLAN) == []


def test_no_intervention_still_has_a_receipt_with_its_reason():
    rec = receipt.build(
        task_id="TASK_01", intervened=False, reason=Reason.PAUSE_NO_CUT, event_seq=1
    )
    assert rec["intervened"] is False and rec["reason"] == "PAUSE_NO_CUT"
    assert rec["plan_sha256"] is None and receipt.verify(rec) == []


def test_a_tampered_plan_hash_does_not_verify():
    rec = _intervened()
    rec["plan_sha256"] = "0" * 64
    assert any("receipt_sha256" in p for p in receipt.verify(rec, PLAN))
    rec["receipt_sha256"] = receipt.digest(rec)  # re-sealed: the plan itself no longer matches
    assert any("plan_sha256" in p for p in receipt.verify(rec, PLAN))
    assert receipt.verify(_intervened(), PLAN + "3. also run rm\n") != []


@pytest.mark.parametrize(
    "over",
    [
        dict(intervened=False),  # no reason
        dict(intervened=False, reason="PAUSE_SOMETHING_ELSE"),
        dict(reason=Reason.PAUSE_PLANNER_TIMEOUT),  # a fallback cannot have intervened
        dict(gate_verdict="REJECT"),  # a rejected plan is never delivered
        dict(plan=None),
        dict(event_seq=True),
        dict(event_seq=-1),
        dict(checkpoint_sha256=None),  # an intervention names its checkpoint
        dict(checkpoint_sha256="C" * 64),  # not lowercase hex
        dict(checkpoint_sha256="c" * 63),
        dict(checkpoint_sha256="checkpoint"),
        dict(plan_sha256="0" * 64),  # build computes it from the plan: never overridden
        dict(plan_sha256=hashlib.sha256(PLAN.encode()).hexdigest()),
        dict(schema="cairnwork.pause.receipt/v0"),
    ],
)
def test_inconsistent_receipts_are_refused(over):
    with pytest.raises(ValueError):
        _intervened(**over)


def test_a_guard_rollback_may_trigger_an_intervention():
    rec = _intervened(reason=Reason.PAUSE_GUARD_ROLLED_BACK, guard_rollbacks=1)
    assert receipt.verify(rec, PLAN) == []


def test_unknown_or_missing_fields_do_not_verify():
    rec = _intervened()
    assert receipt.verify({**rec, "extra": 1}) != []
    assert receipt.verify({k: v for k, v in rec.items() if k != "gate_effort"}) != []


def test_append_keeps_the_event_order(tmp_path):
    log = tmp_path / "receipts.jsonl"
    receipt.append(log, _intervened(event_seq=2))
    receipt.append(
        log, receipt.build(task_id="T2", intervened=False, reason="PAUSE_NO_CUT", event_seq=5)
    )
    rows = [json.loads(x) for x in log.read_text().splitlines()]
    assert [r["event_seq"] for r in rows] == [2, 5] and receipt.verify_log(rows) == []
    with pytest.raises(ValueError, match="event_seq"):
        receipt.append(log, _intervened(event_seq=5))
    bad = {**rows[0], "gate_effort": "high"}
    with pytest.raises(ValueError, match="receipt_sha256"):
        receipt.append(log, bad)
    assert len(log.read_text().splitlines()) == 2
    assert receipt.verify_log([rows[1], rows[0]]) != []


@pytest.mark.parametrize("bad", ["0" * 63, "A" * 64, "z" * 64, None])
def test_a_resealed_receipt_with_a_malformed_hash_does_not_verify(bad):
    # Codex review of #530: verify without the plan still checks both hashes' shape
    for field in ("plan_sha256", "checkpoint_sha256"):
        rec = {**_intervened(), field: bad}
        rec["receipt_sha256"] = receipt.digest(rec)
        assert any(field in p for p in receipt.verify(rec)), (field, bad)
    rec = receipt.build(task_id="T", intervened=False, reason="PAUSE_NO_CUT", event_seq=1)
    rec = {**rec, "checkpoint_sha256": "xyz"}
    rec["receipt_sha256"] = receipt.digest(rec)
    assert any("checkpoint_sha256" in p for p in receipt.verify(rec))
