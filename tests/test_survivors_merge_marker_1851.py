"""#1851: when merging two Survivors drops listed rows (over MAX_LISTED), the merged value says so
(``listing=incomplete``, the word #1850 uses). Counts and the verdict are unchanged; a merge that
drops nothing carries no marker."""

from cairnwork.survivor_argv import MAX_LISTED, Survivors, listing

MARK = "listing=incomplete"


def _rows(n: int, tag: str = "p") -> tuple[str, ...]:
    return tuple(f"{tag}{i}" for i in range(n))


def test_merge_over_the_limit_carries_the_marker():
    got = Survivors(MAX_LISTED, _rows(MAX_LISTED)) + Survivors(2, _rows(2, "q"))
    assert len(got.argv) == MAX_LISTED
    assert MARK in got.reasons
    assert MARK in listing(got)


def test_merge_within_the_limit_has_no_marker():
    got = Survivors(2, _rows(2)) + Survivors(2, _rows(2, "q"))
    assert MARK not in got.reasons
    assert MARK not in listing(got)


def test_merge_exactly_at_the_limit_has_no_marker():
    got = Survivors(MAX_LISTED - 1, _rows(MAX_LISTED - 1)) + Survivors(1, _rows(1, "q"))
    assert len(got.argv) == MAX_LISTED
    assert MARK not in got.reasons


def test_counts_and_verdict_do_not_change():
    got = Survivors(MAX_LISTED, _rows(MAX_LISTED)) + Survivors(3, _rows(3, "q"))
    assert int(got) == MAX_LISTED + 3
    assert got == MAX_LISTED + 3


def test_a_plain_int_added_over_a_full_list_adds_no_marker():
    got = Survivors(MAX_LISTED, _rows(MAX_LISTED)) + 4
    assert int(got) == MAX_LISTED + 4
    assert MARK not in got.reasons


def test_the_marker_survives_a_later_merge():
    full = Survivors(MAX_LISTED, _rows(MAX_LISTED)) + Survivors(1, _rows(1, "q"))
    assert MARK in (full + Survivors(0)).reasons
    assert (full + Survivors(0)).reasons.count(MARK) == 1
