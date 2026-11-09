"""#775 Step 1: a base run whose target tests failed only because of the environment (a fixture
that raised, an OSError in the body) is UNCOMPARABLE, not TEETH; a candidate run that fails for the
same reason says so in its reason. Existing teeth, teardown (#478) and not-run (#660) rows stay in
test_base_comparison.py, unchanged."""

from types import SimpleNamespace
from typing import Any

from cairnwork.deferred_tests import base_comparison
from cairnwork.reasons import why_not_passed

T1 = "tests/test_regression.py::test_one"
T2 = "tests/test_regression.py::test_two"
TARGETS = frozenset({T1, T2})


def base(outcomes, fail_kind=None, rc=1):
    return SimpleNamespace(
        spec=SimpleNamespace(verifier_id="v"), returncode=rc, outcomes=outcomes,
        timed_out=False, report_too_big=False, recorded=True, collect_errors=(), mutated=(),
        teardown_errors=(), survivors=0, forged=(), log_too_big=False,
        fail_kind=fail_kind or {},
    )  # fmt: skip


def judge(outcomes, fail_kind):
    return base_comparison([base(outcomes, fail_kind)], TARGETS)


def test_all_targets_with_a_setup_error_are_uncomparable_with_the_import_hint():
    got = judge({T1: "failed", T2: "failed"}, {T1: "setup", T2: "setup"})
    assert (got.kind, got.code) == ("UNCOMPARABLE", "BASE_SETUP_ERROR")
    assert "test_one" in got.detail and "inside the test function" in got.detail


def test_all_targets_with_an_oserror_in_the_body_are_uncomparable():
    got = judge({T1: "failed", T2: "failed"}, {T1: "env", T2: "env"})
    assert (got.kind, got.code) == ("UNCOMPARABLE", "BASE_ENV_ERROR")
    assert "test_one" in got.detail


def test_a_setup_error_is_named_before_an_env_error():
    got = judge({T1: "failed", T2: "failed"}, {T1: "env", T2: "setup"})
    assert got.code == "BASE_SETUP_ERROR"


def test_an_assertion_failure_is_still_teeth():
    assert judge({T1: "failed", T2: "passed"}, {}).kind == "TEETH"


def test_one_target_with_teeth_wins_over_another_with_a_setup_error():
    assert judge({T1: "failed", T2: "failed"}, {T2: "setup"}).kind == "TEETH"


def test_one_target_with_teeth_wins_over_another_with_an_env_error():
    assert judge({T1: "failed", T2: "failed"}, {T2: "env"}).kind == "TEETH"


def test_an_import_error_in_the_body_is_teeth():
    """The documented pattern (import the fix-only name inside the test) records no fail_kind."""
    assert judge({T1: "failed", T2: "passed"}, {}).kind == "TEETH"


def test_a_parametrisation_with_an_env_error_does_not_hide_one_with_an_assertion():
    outcomes = {f"{T1}[a]": "failed", f"{T1}[b]": "failed", T2: "passed"}
    assert judge(outcomes, {f"{T1}[a]": "env"}).kind == "TEETH"


def test_every_parametrisation_with_an_env_error_is_uncomparable():
    outcomes = {f"{T1}[a]": "failed", f"{T1}[b]": "failed", T2: "passed"}
    got = judge(outcomes, {f"{T1}[a]": "env", f"{T1}[b]": "env"})
    assert got.code == "BASE_ENV_ERROR"


def test_a_failure_kind_on_a_test_that_is_not_a_target_gives_no_teeth_and_no_block():
    other = "tests/test_old.py::test_old"
    got = judge({T1: "passed", T2: "passed", other: "failed"}, {other: "env"})
    assert got.kind == "NO_TEETH"


def test_a_passing_target_with_a_stray_kind_is_no_teeth():
    other = "tests/test_old.py::test_old"  # exit 1 needs some failed test in the record
    assert judge({T1: "passed", T2: "passed", other: "failed"}, {T1: "env"}).kind == "NO_TEETH"


def test_a_second_run_with_teeth_still_gives_teeth():
    runs = [base({T1: "failed", T2: "failed"}, {T1: "env", T2: "env"}), base({T1: "failed"})]
    runs[1].outcomes[T2] = "passed"
    assert base_comparison(runs, TARGETS).kind == "TEETH"


BASE: dict[str, Any] = dict(
    ran_sandboxed=True, timed_out=False, mutated=(), survivors=0, log_too_big=False,
    tests_ran=False, exitstatus=1, testsfailed=1,
)  # fmt: skip


def test_the_candidate_reason_names_a_setup_error_on_a_target():
    why = why_not_passed(**BASE, returncode=1, fail_kind={T1: "setup"})
    assert "setup error in" in why and "test_one" in why and "pytest exit code 1" in why


def test_the_candidate_reason_names_an_environment_error_on_a_target():
    why = why_not_passed(**BASE, returncode=1, fail_kind={T2: "env"})
    assert (
        "environment error (OSError, MemoryError, RecursionError, child) in" in why
        and "test_two" in why
    )


def test_the_candidate_reason_is_unchanged_without_a_kind():
    assert why_not_passed(**BASE, returncode=1, fail_kind={}) == "pytest exit code 1"


def test_a_target_skipped_on_the_base_is_not_teeth():
    """Only ``failed`` gives teeth (a mutation to "anything but passed" must turn this red)."""
    other = "tests/test_old.py::test_old"  # exit 1 needs some failed test in the record
    got = judge({T1: "skipped", T2: "passed", other: "failed"}, {})
    assert got.kind == "NO_TEETH"
