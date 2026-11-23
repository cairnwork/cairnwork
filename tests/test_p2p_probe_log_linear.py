"""Long-line timing of ``mask_secrets`` (#1329), judged by growth, not by a wall-clock limit.

A busy runner slows the short and the long input alike, so the ratio stays put where a fixed
seconds limit goes red. The limit is a coarse net: it catches clearly quadratic work (about 16
for 4x the input) but not every mild super-linear slowdown; a mutation run caught 1 of 6 mild
quadratic variants, so a small quadratic term can pass. Linear work gives about 4.
"""

from __future__ import annotations

import time
from collections.abc import Callable

import pytest


def _growth(
    work, small: int = 16384, runs: int = 3, clock: Callable[[], float] = time.perf_counter
) -> float:
    """Time of ``work(text)`` on a text 4x as long over the time on the short one (best of
    ``runs`` each). About 4 for linear work, about 16 for quadratic; a slow or busy machine slows
    both sides alike, so no wall-clock limit is involved."""

    def best(size: int) -> float:
        times = []
        for _ in range(runs):
            started = clock()
            work(size)
            times.append(clock() - started)
        return min(times)

    return best(small * 4) / max(best(small), 1e-6)


GROWTH_LIMIT = 10  # linear is about 4, quadratic about 16


@pytest.mark.parametrize("unit", ["-", "a-", "a_", "x-token-", "a＿", "a︳"])
def test_one_long_line_of_word_characters_and_dashes_is_not_quadratic(unit):
    from cairnwork.p2p_probe_log import mask_secrets

    def work(size: int) -> None:
        out = mask_secrets("head\n" + unit * (size // len(unit)) + "\nEND\n")
        assert out.endswith("END\n")

    assert _growth(work) < GROWTH_LIMIT


def test_the_growth_check_catches_quadratic_work_and_passes_linear_work():
    """Control: the same check reports quadratic work as growing past the limit. The work counts
    the characters it touches and the check reads that count as its clock, so no wall-clock
    time is involved and a busy runner cannot turn this red."""
    touched = [0]

    def quadratic(size: int) -> None:
        for i in range(0, size, 4):
            touched[0] += size - i  # the length of text[i:], which a rescan would walk

    def linear(size: int) -> None:
        touched[0] += size

    def clock() -> float:
        return float(touched[0])

    assert _growth(quadratic, small=4096, clock=clock) > GROWTH_LIMIT
    assert _growth(linear, small=1_000_000, clock=clock) < GROWTH_LIMIT
