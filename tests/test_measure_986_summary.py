"""scripts/measure_986_summary.py: the AC8 numbers from the rows of tests/measure_986_ac8.py, and
INVALID (non-zero exit) whenever the rows cannot support them."""

import importlib.util
import json
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "measure_986_summary", Path(__file__).parent.parent / "scripts" / "measure_986_summary.py"
)
assert _spec is not None and _spec.loader is not None
summary = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(summary)


def _row(i, arm, scenario, seconds, status, refused=False, probe=None):
    return {
        "i": i,
        "arm": arm,
        "scenario": scenario,
        "seconds": seconds,
        "status": status,
        "refused_conftest": refused,
        "uncomparable": "",
        "probe": probe,
    }


def _good_rows(n=20):
    rows = []
    for i in range(n):  # the shape of the first dry run: a bad candidate refused early is cheap
        rows += [
            _row(i, "off", "bad", 25.4, "COMPLETED"),
            _row(i, "on", "bad", 5.4, "FAILED", refused=True, probe="OK"),
            _row(i, "off", "good", 24.3, "COMPLETED"),
            _row(i, "on", "good", 28.1, "COMPLETED", probe="OK"),
        ]
    return rows


def test_a_clean_run_reports_the_good_candidate_time_only():
    s = summary.summarise(_good_rows())
    assert s["invalid"] == []
    assert s["end_to_end_increase"] == 0.1564  # 28.1 / 24.3 - 1, not mixed with the bad candidate
    assert s["added_p95_s"] == 3.8 and s["added_max_s"] == 3.8
    assert s["collection_success"] == 1.0
    assert s["new_false_passes"] == 0 and s["false_blocks_on"] == 0 and s["errors"] == 0


def _drop_one(rows):
    return rows[:-1]


def _only_off(rows):
    return [r for r in rows if r["arm"] == "off"]


def _error_row(rows):
    rows[1] = _row(0, "on", "bad", 1.0, "ERROR")
    return rows


@pytest.mark.parametrize(
    "make",
    [lambda rows: [], _only_off, _drop_one, lambda rows: rows[:-4]],
    ids=["empty", "one-arm-only", "79-of-80", "a-pair-short"],
)
def test_rows_that_cannot_support_the_numbers_are_invalid(make):
    assert summary.summarise(make(_good_rows()))["invalid"]


def test_too_few_pairs_is_invalid_unless_lowered():
    assert summary.summarise(_good_rows(3))["invalid"]
    assert summary.summarise(_good_rows(3), min_n=1)["invalid"] == []


def test_an_error_row_is_counted_not_dropped():
    s = summary.summarise(_error_row(_good_rows()))
    assert s["errors"] == 1 and s["bad_not_refused_on"] == 1
    assert s["invalid"] == []  # the row exists: it is a result, shown, not hidden


def test_a_blocked_good_candidate_and_a_missed_probe_are_counted():
    rows = _good_rows()
    rows[3] = _row(0, "on", "good", 28.1, "FAILED", refused=True, probe="RUN_FAULT")
    s = summary.summarise(rows)
    assert s["false_blocks_on"] == 1
    assert s["collection_success"] == 39 / 40  # the good candidates' probes count too
    assert s["fallback_reasons"] == {"RUN_FAULT": 1}


def test_a_bad_candidate_accepted_only_with_the_step_on_is_a_new_false_pass():
    rows = _good_rows()
    rows[0] = _row(0, "off", "bad", 25.4, "FAILED", refused=True)
    rows[1] = _row(0, "on", "bad", 5.4, "COMPLETED", probe="OK")
    assert summary.summarise(rows)["new_false_passes"] == 1


def test_main_exits_nonzero_on_invalid_and_zero_on_valid(tmp_path, capsys):
    ok, bad = tmp_path / "ok.jsonl", tmp_path / "bad.jsonl"
    ok.write_text("".join(json.dumps(r) + "\n" for r in _good_rows()))
    bad.write_text("")
    assert summary.main(["x", str(ok)]) == 0
    assert summary.main(["x", str(bad)]) == 1
    assert "INVALID" in capsys.readouterr().out


def test_the_cold_first_pair_is_a_warm_up_and_left_out_of_the_time():
    rows = _good_rows()
    rows[3] = _row(0, "on", "good", 90.0, "COMPLETED", probe="OK")  # pair 0 starts cold
    s = summary.summarise(rows)
    assert s["added_max_s"] == 3.8 and s["end_to_end_increase"] == 0.1564


def test_the_requested_pair_count_decides_validity_not_what_was_seen():
    rows = _good_rows(20)
    assert summary.summarise(rows, expect_n=20)["invalid"] == []
    assert summary.summarise(rows, expect_n=40)["invalid"]  # cancelled after 20 of 40 pairs
    assert summary.summarise(rows, expect_n=19)["invalid"]  # more than asked for


def test_a_repeated_case_is_invalid():
    rows = _good_rows()
    assert summary.summarise([*rows, rows[5]])["invalid"]


def test_a_good_candidate_that_errored_in_either_arm_is_not_timed_but_is_counted():
    rows = _good_rows()
    for k, r in enumerate(rows):
        if r["scenario"] == "good" and r["arm"] == "on" and 1 <= r["i"] <= 10:
            rows[k] = _row(r["i"], "on", "good", 1.0, "ERROR", probe="OK")
    s = summary.summarise(rows, expect_n=20)
    assert s["end_to_end_increase"] == 0.1564 and s["timed_pairs"] == 9
    assert s["errors"] == 10 and s["false_blocks_on"] == 10 and s["invalid"] == []


def test_p95_and_max_of_varying_pair_differences_with_negatives():
    diffs = {0: 90.0, 1: -3.0, 2: -1.0, **{i: i / 2 for i in range(3, 21)}}  # pair 0 is cold
    rows = []
    for i in range(21):
        rows += [
            _row(i, "off", "bad", 25.0, "COMPLETED"),
            _row(i, "on", "bad", 5.0, "FAILED", refused=True, probe="OK"),
            _row(i, "off", "good", 24.0, "COMPLETED"),
            _row(i, "on", "good", 24.0 + diffs[i], "COMPLETED", probe="OK"),
        ]
    s = summary.summarise(rows, expect_n=21)
    assert s["timed_pairs"] == 20
    assert s["added_p95_s"] == 9.5 and s["added_max_s"] == 10.0  # 19th of 20, then the largest
