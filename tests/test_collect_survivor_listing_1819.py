"""#1819 step 1: a collect-only refusal for survivors names them (argv prefixes and fixed reasons,
the listing the deferred-test base path already prints), so the refusal can be diagnosed from its
output. Every other refusal word, and a bare count, stay exactly as they were."""

import pytest
from test_collect_reader import fake

from cairnwork.collect_reader import Collected, read_collected
from cairnwork.survivor_argv import Survivors


def test_a_survivor_refusal_carries_the_listing():
    got = read_collected(fake(survivors=Survivors(2, ("sleep 30", "python -m svc"), ("alive",))))
    assert got.unusable == "collect-survivors (argv: sleep 30, python -m svc; reason: alive)"
    assert got == Collected(unusable=got.unusable)  # still no ids from a run with survivors


@pytest.mark.parametrize("survivors", [1, Survivors(1), Survivors(3, (), ())])
def test_a_survivor_count_without_evidence_keeps_the_bare_reason(survivors):
    assert read_collected(fake(survivors=survivors)).unusable == "collect-survivors"


@pytest.mark.parametrize(
    ("change", "why"),
    [
        ({"timed_out": True}, "collect-timeout"),
        ({"forged": ("x",)}, "collect-forged"),
        ({"mutated": ("src/a.py",)}, "collect-mutated"),
        ({"returncode": 1}, "collect-rc-1"),
    ],
)
@pytest.mark.parametrize("count", [0, 2])
def test_other_refusals_keep_their_own_word_and_no_listing(change, why, count):
    got = read_collected(fake(survivors=Survivors(count, ("sleep 30",), ("alive",)), **change))
    if count and why in ("collect-mutated", "collect-rc-1"):  # survivors rank before these
        why = "collect-survivors (argv: sleep 30; reason: alive)"
    assert got.unusable == why
