"""Boundaries of the deny judge's volume and stall rules, replayed from ndjson (Refs #995)."""

import pytest
from test_deny_channel import line, stream, window

from cairnwork import deny_channel, deny_judge
from cairnwork.deny_channel import judge

FOREIGN = "Sandbox: flood(8) deny(1) network-outbound remote:*:9"
SHORT = 2  # the opening and the closing canary: one line each


def burst(count):
    return line(f"{count} duplicate reports for {FOREIGN}", tag="")


@pytest.mark.parametrize("extra, state", [(0, "CLEAN"), (1, "UNAVAILABLE")])
def test_one_second_bucket_exactly_at_the_cap_is_not_over(extra, state):
    # the opening canary is already one line of second 0
    evidence = judge(stream(burst(deny_channel.MAX_LINES_PER_SECOND - 1 + extra)), window())
    assert evidence.state == state
    assert evidence.peak_per_second == deny_channel.MAX_LINES_PER_SECOND + extra


@pytest.mark.parametrize("closing, state", [(10, "CLEAN"), (11, "UNAVAILABLE")])
def test_a_gap_exactly_at_the_stall_limit_is_not_a_stall(closing, state):
    assert deny_judge.STALL_SECONDS == 10.0
    assert judge(stream(closing=closing), window()).state == state


@pytest.mark.parametrize("extra, state", [(0, "CLEAN"), (1, "UNAVAILABLE")])
def test_another_process_s_allow_report_counts_its_n_toward_the_window_total(extra, state):
    seconds, per_second = 5, 900  # every bucket stays under the per-second cap
    cap = deny_channel.MAX_WINDOW_LINES + seconds * deny_channel.BACKGROUND_PER_SECOND
    allow = "Sandbox: y(87) allow(1) file-read-data /b"

    def many(count, second):
        return line(f"{count} duplicate reports for {allow}", second=second, tag="")

    rest = cap - SHORT - 2 * per_second + extra
    body = [many(per_second, 1), many(per_second, 2), many(rest, 3)]
    evidence = judge(stream(*body, closing=seconds - 1), window())
    assert evidence.state == state and evidence.window_lines == cap + extra
