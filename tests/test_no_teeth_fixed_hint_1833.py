"""#1833: a hidden-run refusal "tests do not fail on base" carries one fixed sentence, chosen by
the code alone (same bytes whatever the input), so the author reads why an imported-base class
may give no test ids without any text of the tests leaking (#1688 stays: code + count + this)."""

import pytest

from cairnwork.deferred_overlay import TestCommitRejected, author_reason

CODE = "TESTS_DO_NOT_FAIL_ON_BASE"
WITHHELD = "{}: 1 refusal (detail withheld)"
DETAILS = [
    "pytest exit codes [0]",
    "pytest exit codes [1, 1]",
    "t_secret/test_CANARY1833.py::TestSecret::test_x imported from helpers",
    "3 test class(es) in the changed files",
    "x" * 500,
]
OTHER = [
    "DEFERRED_TESTS_NOT_RUN",
    "DEFERRED_TESTS_UNREADABLE",
    "DEFERRED_TESTS_NOT_TARGETED",
    "TEST_COMMIT_SCOPE",
    "TEST_COMMIT_UNPARSABLE",
    "OVERLAY_UNSUPPORTED_ENTRY",
]


def test_the_author_reads_the_fixed_sentence_after_the_withheld_count():
    reason = author_reason(TestCommitRejected(CODE, DETAILS[0]), True)
    assert reason.startswith(WITHHELD.format(CODE) + "; ")
    assert "imported" in reason and "Test*" in reason and "*TestCase" in reason


def test_different_input_with_the_same_code_gives_the_same_reason_byte_for_byte():
    reasons = {author_reason(TestCommitRejected(CODE, d), True) for d in DETAILS}
    assert len(reasons) == 1
    assert "CANARY1833" not in next(iter(reasons))
    assert not any(ch.isdigit() for ch in next(iter(reasons)).replace("1 refusal", ""))  # no count


@pytest.mark.parametrize("code", OTHER)
def test_every_other_code_is_unchanged(code):
    exc = TestCommitRejected(code, "detail CANARY1833")
    assert author_reason(exc, True) == WITHHELD.format(code)


@pytest.mark.parametrize("detail", DETAILS)
def test_a_visible_run_stays_str_of_the_exception(detail):
    exc = TestCommitRejected(CODE, detail)
    assert author_reason(exc, False) == str(exc)


def test_no_detail_stays_the_bare_code():
    exc = TestCommitRejected(CODE)
    assert author_reason(exc, True) == str(exc) == CODE
