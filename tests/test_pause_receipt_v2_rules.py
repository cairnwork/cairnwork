"""Mutation-sensitive coverage for the PAUSE receipt v2 invariants."""

from __future__ import annotations

from typing import Any

import pytest

from experimental.pause import receipt
from experimental.pause.schema import GATE_VERDICTS, Reason


def _intervened(**over: Any) -> Any:
    fields: dict[str, Any] = {
        "task_id": "TASK_V2",
        "intervened": True,
        "event_seq": 1,
        "checkpoint_sha256": "a" * 64,
        "plan": "make the scoped change",
        "gate_verdict": "ACCEPT",
        "core_verdict": "VERIFIED",
        "core_tree_sha256": "b" * 64,
    }
    return receipt.build(**{**fields, **over})


def _fallback(**over: Any) -> Any:
    fields: dict[str, Any] = {
        "task_id": "TASK_V2",
        "intervened": False,
        "event_seq": 1,
        "reason": Reason.PAUSE_NO_CUT,
        "core_verdict": None,
        "core_tree_sha256": None,
    }
    return receipt.build(**{**fields, **over})


def _reseal(rec: Any) -> Any:
    changed = dict(rec)
    if any(isinstance(value, bytes) for value in changed.values()):
        changed["receipt_sha256"] = "unsealed"
    else:
        changed["receipt_sha256"] = receipt.digest(changed)
    return changed


@pytest.mark.parametrize(
    ("gate_verdict", "intervened", "reason", "accepted"),
    [
        ("accept", True, None, False),
        ("", True, None, False),
        ("ACCEPT ", True, None, False),
        ("MAYBE", True, None, False),
        (1, True, None, False),
        (True, True, None, False),
        (None, False, Reason.PAUSE_NO_CUT, True),
        ("ACCEPT", True, None, True),
        ("REJECT", False, Reason.PAUSE_NO_CUT, True),
    ],
)
def test_gate_verdict_allow_list(gate_verdict: Any, intervened: bool, reason: Any, accepted: bool):
    values: dict[str, Any] = {"gate_verdict": gate_verdict}
    if intervened:
        values.update(
            checkpoint_sha256="a" * 64,
            plan="plan",
            core_verdict="VERIFIED",
            core_tree_sha256="b" * 64,
        )
    else:
        values.update(reason=reason)

    if accepted:
        rec = _intervened(**values) if intervened else _fallback(**values)
        assert rec["gate_verdict"] in (None, *GATE_VERDICTS)
        assert receipt.verify(rec) == []
    else:
        with pytest.raises(ValueError, match="gate_verdict"):
            if intervened:
                _intervened(**values)
            else:
                _fallback(**values)
        valid = _intervened()
        assert any(
            "gate_verdict" in problem
            for problem in receipt.verify(_reseal({**valid, "gate_verdict": gate_verdict}))
        )


@pytest.mark.parametrize(
    ("core_verdict", "accepted"),
    [
        (None, False),
        ("FAILED", False),
        ("verified", False),
        (False, False),
        (1, False),
        (b"VERIFIED", False),
        (type("Verdict", (str,), {})("VERIFIED"), False),
        ("VERIFIED", True),
    ],
)
def test_intervention_core_verdict_type_and_value(core_verdict: Any, accepted: bool):
    if accepted:
        rec = _intervened(core_verdict=core_verdict)
        assert receipt.verify(rec) == []
        return

    with pytest.raises(ValueError):
        _intervened(core_verdict=core_verdict)
    altered = _reseal({**_intervened(), "core_verdict": core_verdict})
    problems = receipt.verify(altered)
    assert problems
    if isinstance(core_verdict, str) and core_verdict != "VERIFIED":
        assert any("bound Core VERIFIED" in problem for problem in problems)
    elif core_verdict is not None:
        assert any("core_verdict" in problem for problem in problems)


@pytest.mark.parametrize(
    ("tree", "accepted"),
    [
        (True, False),
        (1, False),
        (b"b" * 64, False),
        ("B" * 64, False),
        ("b" * 63, False),
        ("b" * 64, True),
    ],
)
def test_core_tree_digest_shape(tree: Any, accepted: bool):
    if accepted:
        rec = _intervened(core_tree_sha256=tree)
        assert receipt.verify(rec) == []
        return

    with pytest.raises(ValueError):
        _intervened(core_tree_sha256=tree)
    altered = _reseal({**_intervened(), "core_tree_sha256": tree})
    assert any("core_tree_sha256" in p for p in receipt.verify(altered))


@pytest.mark.parametrize(
    ("intervened", "core_verdict", "tree", "reason", "accepted"),
    [
        (True, None, "b" * 64, None, False),
        (True, "FAILED", "b" * 64, None, False),
        (True, "VERIFIED", None, None, False),
        (True, "VERIFIED", "b" * 64, None, True),
        (False, "VERIFIED", "b" * 64, Reason.PAUSE_NO_CUT, False),
        (False, None, "b" * 64, Reason.PAUSE_NO_CUT, False),
        (False, "FAILED", None, Reason.PAUSE_NO_CUT, False),
        (False, None, None, Reason.PAUSE_NO_CUT, True),
        (False, "FAILED", "b" * 64, Reason.PAUSE_NO_CUT, True),
    ],
)
def test_intervened_and_fallback_core_binding(
    intervened: bool, core_verdict: Any, tree: Any, reason: Any, accepted: bool
):
    if intervened:
        fields: dict[str, Any] = {
            "core_verdict": core_verdict,
            "core_tree_sha256": tree,
        }
    else:
        fields = {
            "core_verdict": core_verdict,
            "core_tree_sha256": tree,
            "reason": reason,
        }

    if accepted:
        rec = _intervened(**fields) if intervened else _fallback(**fields)
        assert rec["intervened"] is intervened
        assert receipt.verify(rec) == []
        return

    with pytest.raises(ValueError):
        if intervened:
            _intervened(**fields)
        else:
            _fallback(**fields)
    base = _intervened() if intervened else _fallback()
    altered = _reseal(
        {
            **base,
            "intervened": intervened,
            "core_verdict": core_verdict,
            "core_tree_sha256": tree,
            "reason": reason if not intervened else None,
        }
    )
    assert receipt.verify(altered)


def test_build_round_trips_every_valid_v2_core_binding_shape():
    receipts = [
        _intervened(),
        _fallback(),
        _fallback(core_verdict="FAILED", core_tree_sha256="c" * 64),
    ]
    assert all(receipt.verify(rec) == [] for rec in receipts)
