"""Round-ten closed-position tests re-exported by the pinned witness entry suite."""

from __future__ import annotations

import pytest

from tests import _w1353_allow_positions as positions
from tests._w1353_allow import check
from tests._w1353_allow_round10_cases import (
    ALIAS_GRAMMAR_BLOCK,
    EXTRA,
    LEGACY_CAUGHT_IDS,
    ROUND_TEN_BLOCK,
    ROUND_TEN_MUTATION_IDS,
    source_for,
)

__all__ = (
    "test_round_ten_positions_reject_raw_escape",
    "test_round_ten_covers_prior_alias_grammar",
    "test_round_ten_position_mutation_is_live",
    "test_round_ten_benign_positions_remain_allowed",
)


@pytest.mark.parametrize("case_id", sorted(ROUND_TEN_BLOCK))
def test_round_ten_positions_reject_raw_escape(case_id):
    assert check(source_for(ROUND_TEN_BLOCK[case_id]), "tests/_w1353_extra.py"), case_id


@pytest.mark.parametrize("case_id", sorted(ALIAS_GRAMMAR_BLOCK))
def test_round_ten_covers_prior_alias_grammar(case_id):
    assert check(source_for(ALIAS_GRAMMAR_BLOCK[case_id]), "tests/_w1353_extra.py"), case_id


def test_round_ten_position_mutation_is_live(monkeypatch):
    """Drop only the position findings (keep the raw names the legacy row pins use)."""
    original = positions.position_findings
    monkeypatch.setattr(positions, "position_findings", lambda *args: ([], original(*args)[1]))
    assert not check(EXTRA, "tests/_w1353_extra.py")
    for case_id in ROUND_TEN_MUTATION_IDS:
        findings = check(source_for(ROUND_TEN_BLOCK[case_id]), "tests/_w1353_extra.py")
        assert bool(findings) == (case_id in LEGACY_CAUGHT_IDS), case_id


@pytest.mark.parametrize(
    "source",
    [
        'status = result.get("status", result.get("error", "FAILED"))',
        'verdict = result\nrow("C1", "x", "r", verdict.reason, "FAILED", "REFUSED")',
        'row("C1", "x", "r", result, "FAILED", "REFUSED", result.get("reason") or "")',
        "blackbox_result(result)",
        'ok = result.get("status", "").startswith("VERIFIED")',
        'box = {}\nbox["k"] = 1',
    ],
)
def test_round_ten_benign_positions_remain_allowed(source):
    fixture = "from _w1353_probe import verify, row, blackbox_result\nresult = verify(1)\n" + source
    assert not check(fixture, "tests/_w1353_extra.py")
