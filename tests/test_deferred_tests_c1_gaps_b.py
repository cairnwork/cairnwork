"""More deferred_tests behaviour pinned after mutation testing (C1): the node ids a test commit
adds, how a file is read at a revision, and how base runs become a Comparison."""

from types import SimpleNamespace
from typing import Any

import pytest
from conftest import git
from test_deferred_tests_c1_gaps import base_of, mkcommit, refusal, spy

import cairnwork.deferred_tests as dt

TEST_A = "def test_a():\n    assert True\n"


# ---- new_tests --------------------------------------------------------------------------


def test_a_non_python_path_does_not_stop_the_search_for_new_tests(repo):
    base = git(repo, "rev-parse", "HEAD")
    tc = mkcommit(repo, {"tests/data.json": "{}", "tests/test_a.py": TEST_A}, base)
    paths = ("tests/data.json", "tests/test_a.py")
    assert dt.new_tests(repo, base, tc, paths) == frozenset({"tests/test_a.py::test_a"})


def test_an_unchanged_trusted_testcase_import_is_not_a_hidden_change(repo):
    old = "from unittest import TestCase\n\ndef test_old():\n    assert True\n"
    base = base_of(repo, {"tests/test_a.py": old})
    tc = mkcommit(repo, {"tests/test_a.py": old + TEST_A}, base)
    assert dt.new_tests(repo, base, tc, ("tests/test_a.py",)) == frozenset(
        {"tests/test_a.py::test_a"}
    )


def test_a_new_test_in_a_class_pytest_cannot_collect_is_refused_in_full(repo):
    body = (
        "class TestX:\n    def __init__(self):\n        pass\n\n"
        "    def test_a(self):\n        pass\n"
    )
    base = git(repo, "rev-parse", "HEAD")
    tc = mkcommit(repo, {"tests/test_a.py": body}, base)
    err = refusal(dt.new_tests, repo, base, tc, ("tests/test_a.py",))
    assert str(err) == (
        "DEFERRED_TESTS_NOT_COLLECTED: 1 new test: pytest does not collect a test class with"
        " __init__ or __new__, so no verifier runs it"
    )


# ---- _tree / _test_functions ------------------------------------------------------------


def test_a_missing_file_is_new_even_with_a_colon_or_a_leading_dash_in_its_name(repo):
    base = git(repo, "rev-parse", "HEAD")
    assert dt._tree(repo, f"{base}:tests/a:b.py") is None
    assert dt._tree(repo, f"{base}:-x.py") is None


def test_a_file_that_cannot_be_shown_but_is_listed_is_unreadable_with_the_show_error(
    repo, monkeypatch
):
    calls = spy(
        monkeypatch,
        fail=lambda a: "show failed" if a[0] == "show" else False,
        reply=lambda a: "100644 blob x\ttests/p.py" if a[0] == "ls-tree" else None,
    )
    err = refusal(dt._tree, repo, "rev:tests/p.py")
    assert str(err) == "TEST_COMMIT_UNREADABLE: GIT_COMMAND_FAILED: show failed"
    assert calls[1][2:] == ("rev", "--", "tests/p.py")


def test_a_failed_existence_check_is_unreadable_with_its_own_error(repo, monkeypatch):
    spy(monkeypatch, fail=lambda a: "show failed" if a[0] == "show" else "ls failed")
    err = refusal(dt._tree, repo, "rev:tests/p.py")
    assert str(err) == "TEST_COMMIT_UNREADABLE: GIT_COMMAND_FAILED: ls failed"


def test_a_file_known_to_exist_is_unreadable_not_new_when_it_cannot_be_shown(repo, monkeypatch):
    calls = spy(monkeypatch, fail=lambda a: "show failed")
    err = refusal(dt._tree, repo, "rev:tests/p.py", present=True)
    assert str(err) == "TEST_COMMIT_UNREADABLE: GIT_COMMAND_FAILED: show failed"
    assert len(calls) == 1


def test_a_missing_file_has_no_test_functions_unless_it_is_known_to_exist(repo):
    blob = f"{git(repo, 'rev-parse', 'HEAD')}:tests/nope.py"
    assert dt._test_functions(repo, blob) == {}
    err = refusal(dt._test_functions, repo, blob, present=True)
    assert err.code == "TEST_COMMIT_UNREADABLE"


def test_an_unparsable_file_names_its_blob(repo):
    base = git(repo, "rev-parse", "HEAD")
    tc = mkcommit(repo, {"tests/test_a.py": "def (:\n"}, base)
    err = refusal(dt._tree, repo, f"{tc}:tests/test_a.py")
    assert str(err) == f"TEST_COMMIT_UNPARSABLE: {tc}:tests/test_a.py"


# ---- Comparison.worker_reason / _base_problem / base_comparison -------------------------


def comparison(detail: str = "why") -> dt.Comparison:
    return dt.Comparison("UNCOMPARABLE", "BASE_TIMEOUT", detail)


@pytest.mark.parametrize(
    ("isolated", "why", "reason"),
    [
        (True, "shared uid", "UNCOMPARABLE: BASE_TIMEOUT: why"),
        (False, "shared uid", "BASE_TIMEOUT: why (worker not isolated: shared uid)"),
        (None, "shared uid", "BASE_TIMEOUT: why (worker not isolated: shared uid)"),
        (False, "", "BASE_TIMEOUT: why"),
        (None, "", "BASE_TIMEOUT: why"),
    ],
)
def test_only_an_isolated_worker_may_say_uncomparable(isolated: Any, why: str, reason: str):
    assert comparison().worker_reason(isolated=isolated, why=why) == reason


def run(**over: Any) -> SimpleNamespace:
    fields: dict[str, Any] = dict(
        returncode=0, forged=False, timed_out=False, survivors=(), mutated=False,
        report_too_big=False, log_too_big=False, collect_errors=(), recorded=True,
        teardown_errors=(), outcomes={}, fail_kind={}, hidden_tests=False, skipped_deps=(),
        spec=SimpleNamespace(verifier_id="v1", targets=("tests/t.py",)),
    )  # fmt: skip
    return SimpleNamespace(**{**fields, **over})


def test_a_refused_data_execution_is_named_by_its_code():
    assert dt._base_problem(run(data_refused=("x",))) == "DATA_EXEC_REFUSED"
    assert dt._base_problem(run()) == ""
    assert dt._base_problem(run(forged=True, data_refused=("x",))) == "BASE_FORGED_BYTECODE"


def test_every_problem_run_is_listed_in_order_and_joined(monkeypatch):
    first = run(returncode=None, timed_out=True, spec=SimpleNamespace(verifier_id="v1", targets=()))
    second = run(returncode=5, spec=SimpleNamespace(verifier_id="v2", targets=("tests/t.py",)))
    got = dt.base_comparison([first, second], frozenset({"tests/t.py::test_x"}))
    assert got == dt.Comparison(
        "UNCOMPARABLE",
        "BASE_TIMEOUT",
        "v1: BASE_TIMEOUT (exit None); v2: BASE_NO_TESTS (exit 5)"
        "; pytest collected no test in 1: tests/t.py",
    )


def test_surviving_processes_are_listed_only_for_the_survivors_problem():
    left = SimpleNamespace(argv=("sleep",), reasons=())
    alive = dt.base_comparison([run(survivors=left)], frozenset())
    assert alive.code == "BASE_SURVIVORS"
    assert alive.detail == "v1: BASE_SURVIVORS (exit 0) (argv: sleep)"
    late = dt.base_comparison([run(returncode=None, timed_out=True, survivors=left)], frozenset())
    assert late.detail == "v1: BASE_TIMEOUT (exit None)"


def test_tests_no_run_ran_are_listed_with_each_runs_skip_hint():
    target = "tests/t.py::test_x"
    runs = [run(skipped_deps=("m1",)), run(skipped_deps=("m2",))]
    got = dt.base_comparison(runs, frozenset({target}))
    assert got == dt.Comparison(
        "NOT_RUN",
        "DEFERRED_TESTS_NOT_RUN",
        f"no verifier collected or ran ['{target}']"
        "; skipped at collection, could not import 1: m1"
        "; skipped at collection, could not import 1: m2",
    )


def test_a_hidden_run_says_how_many_tests_did_not_run_not_which():
    runs = [run(hidden_tests=True, skipped_deps=("m1",)), run(hidden_tests=True)]
    got = dt.base_comparison(runs, frozenset({"tests/t.py::test_x", "tests/t.py::test_y"}))
    assert got.detail == (
        "no verifier collected or ran 2; skipped at collection, could not import 1 module(s)"
    )


def test_tests_that_all_pass_on_the_base_have_no_teeth_and_show_the_exit_codes():
    target = "tests/t.py::test_x"
    runs = [run(outcomes={target: "passed"}), run(outcomes={target: "passed"}, returncode=0)]
    assert dt.base_comparison(runs, frozenset({target})) == dt.Comparison(
        "NO_TEETH", "TESTS_DO_NOT_FAIL_ON_BASE", "pytest exit codes [0, 0]"
    )
