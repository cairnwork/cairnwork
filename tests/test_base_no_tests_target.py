"""#1784: a BASE_NO_TESTS detail names the verifier's target and says pytest collected no test in
it (exit 5), so a caller who passed a file that holds no tests (a conftest) can see which one. The
code, the kind and every other detail are the ones they were; pytest's own output is not shown."""

from types import SimpleNamespace

from test_base_comparison import TARGET, TARGETS, run

from cairnwork.deferred_tests import base_comparison


def with_targets(name, targets, rc=5, failed=(), **fields):
    stand_in = run(name, rc=rc, failed=failed, **fields)
    stand_in.spec = SimpleNamespace(verifier_id=name, targets=tuple(targets))
    return stand_in


def test_the_detail_names_the_verifier_and_the_target_that_collected_nothing():
    got = base_comparison([with_targets("pytest-0", ["tests/conftest.py"])], TARGETS)
    assert (got.kind, got.code) == ("UNCOMPARABLE", "BASE_NO_TESTS")
    assert got.detail == (
        "pytest-0: BASE_NO_TESTS (exit 5); pytest collected no test in 1: tests/conftest.py"
    )


def test_only_the_run_that_collected_nothing_is_named():
    runs = [with_targets("pytest-0", ["tests/conftest.py"]),
            with_targets("pytest-1", ["tests/test_mini.py"], rc=1, failed=(TARGET,))]  # fmt: skip
    got = base_comparison(runs, TARGETS)
    assert "tests/conftest.py" in got.detail and "test_mini" not in got.detail


def test_a_target_is_cleaned_before_it_reaches_the_reason():
    got = base_comparison([with_targets("v", ["tests/x'\n::error::boom"])], TARGETS)
    assert "\n" not in got.detail and "::error::" not in got.detail


def test_other_codes_keep_their_detail():
    got = base_comparison([with_targets("v", ["tests/a.py"], rc=2, collect_errors=("x",))], TARGETS)
    assert "collected no test" not in got.detail


def test_a_run_without_targets_keeps_the_old_detail():
    assert base_comparison([run("v", rc=5, failed=())], TARGETS).detail == (
        "v: BASE_NO_TESTS (exit 5)"
    )
