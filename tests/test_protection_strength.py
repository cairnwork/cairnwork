"""Test-strength sweep 2: pins for protection.py survivors.

Only declared ``blackbox:<id>`` ids are protected; a lookalike id must not raise the level,
and the strict-mode gate digest must bind both the level found and the minimum asked.
"""

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from cairnwork import protection
from cairnwork.p2p_selection import VERIFIER_IDS as P2P_IDS

LOOKALIKES = [
    "blackbox",
    "blackboxfoo",
    "x-blackbox:y",
    "pytest-blackbox:y",
    " blackbox:y",
    "BLACKBOX:y",
]


@pytest.mark.parametrize("lookalike", LOOKALIKES)
@pytest.mark.parametrize("macos", [False, True])
def test_lookalike_id_is_not_protected(lookalike, macos):
    assert protection.level([lookalike], macos=macos) == protection.NONE
    # one real blackbox check plus a lookalike is partial, never full
    assert protection.level(["blackbox:a", lookalike], macos=False) == protection.PARTIAL


@settings(
    max_examples=60,
    deadline=None,
    derandomize=True,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
)
@given(st.text(max_size=12).filter(lambda s: not s.startswith(protection.PREFIX)))
def test_only_prefix_ids_count_as_protected(name):
    assert protection.level([name, "pytest-0"], macos=False) == protection.NONE
    assert protection.level([protection.PREFIX + name], macos=False) == protection.FULL


@pytest.mark.parametrize("found", protection.LEVELS)
@pytest.mark.parametrize("minimum", protection.REQUIRABLE)
def test_gate_digest_binds_level_and_minimum(found, minimum):
    base = protection.gate(found, minimum)
    for other in protection.LEVELS:
        if other != found:
            assert protection.gate(other, minimum)[2] != base[2]
    for other in protection.REQUIRABLE:
        if other != minimum:
            assert protection.gate(found, other)[2] != base[2]


@pytest.mark.parametrize("verifier_id", P2P_IDS)
def test_p2p_ids_are_reserved(verifier_id):
    assert protection.reserved(verifier_id)
    assert protection.reserved(protection.GATE)
    assert not protection.reserved("pytest-0")
