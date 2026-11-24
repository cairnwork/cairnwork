"""scripts/measure_986_summary.py: the AC8 numbers from the rows of tests/measure_986_ac8.py."""

import importlib.util
from pathlib import Path

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
        "uncomparable": None,
        "probe": probe,
    }


def _good_rows():
    rows = []
    for i in range(20):
        rows += [
            _row(i, "off", "bad", 3.0, "COMPLETED"),
            _row(i, "on", "bad", 4.0, "FAILED", refused=True, probe="OK"),
            _row(i, "off", "good", 3.0, "COMPLETED"),
            _row(i, "on", "good", 3.5, "COMPLETED", probe="OK"),
        ]
    return rows


def test_the_four_ac8_numbers_of_a_clean_run():
    s = summary.summarise(_good_rows())
    assert s["pairs"] == 40
    assert s["collection_success"] == 1.0
    assert s["added_p95_s"] == 1.0 and s["added_max_s"] == 1.0
    assert s["end_to_end_increase"] == round(150.0 / 120.0 - 1, 4)
    assert s["new_false_passes"] == 0 and s["false_blocks_on"] == 0


def test_a_missed_bad_candidate_and_a_blocked_good_one_are_counted():
    rows = _good_rows()
    rows[1] = _row(0, "on", "bad", 4.0, "COMPLETED", probe="RUN_FAULT")  # the step saw nothing
    rows[3] = _row(0, "on", "good", 3.5, "FAILED", refused=True, probe="OK")
    s = summary.summarise(rows)
    assert s["bad_not_refused_on"] == 1
    assert s["false_blocks_on"] == 1
    assert s["collection_success"] == 19 / 20
    assert s["new_false_passes"] == 0  # the off arm accepted it too: not new


def test_a_bad_candidate_accepted_only_with_the_step_on_is_a_new_false_pass():
    rows = [
        _row(0, "off", "bad", 3.0, "FAILED", refused=True),
        _row(0, "on", "bad", 3.0, "COMPLETED", probe="OK"),
    ]
    assert summary.summarise(rows)["new_false_passes"] == 1
