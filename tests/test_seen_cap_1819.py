"""#1819: the raw ps text is capped before parsing (bounded input bounds every later step); a cut
or malformed listing says ``listing=incomplete``; count and verdict never change."""

import time

import cairnwork.verifier_seen as vs


def _rows(n, ppid=1):
    return "".join(f"{10 + i} {ppid} S 00:09 d{i}\n" for i in range(n))


def test_oversized_raw_output_is_cut_and_says_incomplete():
    raw = _rows(vs.MAX_RAW_LINES * 3)
    got = vs.result(3, raw, None, 0, 3)
    assert got == 3 and vs.INCOMPLETE in got.reasons


def test_oversized_raw_output_is_cut_by_size_even_without_many_lines():
    raw = "10 1 S 00:09 " + "x" * (vs.MAX_RAW_CHARS * 2) + "\n"
    got = vs.result(1, raw, None, 0, 1)
    assert got == 1 and vs.INCOMPLETE in got.reasons


def test_a_normal_sized_listing_is_full_and_has_no_marker():
    got = vs.result(3, _rows(3), None, 0, 3)
    assert len(got.argv) == 3 and vs.INCOMPLETE not in got.reasons


def test_exactly_at_the_line_limit_is_not_a_cut():
    got = vs.result(2, _rows(2) + "\n" * (vs.MAX_RAW_LINES - 2), None, 0, 2)
    assert vs.INCOMPLETE not in got.reasons


def test_a_huge_table_ends_quickly():
    start = time.monotonic()
    got = vs.result(3, _rows(500_000), None, 0, 3)
    assert time.monotonic() - start < vs.BUDGET and vs.INCOMPLETE in got.reasons


def test_a_malformed_pid_field_says_incomplete():
    got = vs.result(2, "1" * 40 + " 1 S 00:09 d\n20 1 S 00:09 e\n", None, 0, 2)
    assert got == 2 and vs.INCOMPLETE in got.reasons


def test_a_malformed_row_says_incomplete():
    got = vs.result(2, "garbage\n20 1 S 00:09 e\n", None, 0, 2)
    assert got == 2 and vs.INCOMPLETE in got.reasons


def test_before_not_above_main_drops_nothing():
    got = vs.result(1, "20 5 S 00:09 e\n", None, 1, 1)  # parent 5 is unseen: it looks like main
    assert len(got.argv) == 1 and vs.UNRESOLVED not in got.reasons


def test_the_parse_checks_the_time_for_every_line(monkeypatch):
    ticks = iter([0.0, 0.0, 100.0, 100.0, 100.0])
    monkeypatch.setattr(vs.time, "monotonic", lambda: next(ticks))
    assert vs._parse(_rows(3), 50.0) is None


def test_a_raising_path_helper_says_exempt_unknown(monkeypatch):
    monkeypatch.setattr(vs.sys, "platform", "darwin")

    def boom(pids):
        raise OSError("no")

    got = vs.result(1, _rows(1), boom, 0, 1)
    assert vs.UNKNOWN in got.reasons
