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
    "change",
    [{"timed_out": True}, {"forged": ("x",)}, {"mutated": ("src/a.py",)}, {"returncode": 1}],
)
def test_other_refusals_never_carry_a_survivor_listing(change):
    got = read_collected(fake(survivors=Survivors(0, ("sleep 30",), ("alive",)), **change))
    assert "argv" not in got.unusable
