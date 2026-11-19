"""Long-line timing of ``mask_secrets`` (#1329), judged by growth, not by a wall-clock limit.

A busy runner slows the short and the long input alike, so the ratio stays put where a fixed
seconds limit goes red. The limit is a coarse net: it catches clearly quadratic work (about 16
for 4x the input) but not every mild super-linear slowdown; a mutation run caught 1 of 6 mild
quadratic variants, so a small quadratic term can pass. Linear work gives about 4.
"""

from __future__ import annotations

import time

import pytest


def _growth(work, small: int = 16384, runs: int = 3) -> float:
    """Time of ``work(text)`` on a text 4x as long over the time on the short one (best of
    ``runs`` each). About 4 for linear work, about 16 for quadratic; a slow or busy machine slows
    both sides alike, so no wall-clock limit is involved."""

    def best(size: int) -> float:
        times = []
        for _ in range(runs):
            started = time.perf_counter()
            work(size)
            times.append(time.perf_counter() - started)
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
    """Control: the same check reports a quadratic function as growing past the limit."""

    def quadratic(size: int) -> None:
        text = "-" * size
        for i in range(0, size, 4):
            text[i:].count("-")

    def linear(size: int) -> None:
        ("-" * size).count("-")

    assert _growth(quadratic, small=4096) > GROWTH_LIMIT
    assert _growth(linear, small=1_000_000) < GROWTH_LIMIT
