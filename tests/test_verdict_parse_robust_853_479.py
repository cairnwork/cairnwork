import pytest

from cairnwork import report_guard
from cairnwork.verdict_record import well_formed


@pytest.mark.parametrize("bad", [[], {}, 1, True, None])
def test_non_string_fail_kind_is_malformed(bad):
    result = well_formed(
        {"fail_kind": {"test": bad}},
        ["test"],
        {"test": "failed"},
        frozenset({"plugin:example"}),
    )

    assert result is None


def test_deeply_nested_json_returns_not_run():
    raw = b"[" * 200_000

    verdict = report_guard.parse_verdict(raw, "n")

    assert verdict == report_guard.Verdict(False, {})
