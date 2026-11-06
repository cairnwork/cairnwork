"""#798 half 2: a test commit may add a fixture to a conftest.py, and the base tests must not
notice. A reviewer builds a counterexample by changing the ``addition`` (the text appended to the
base conftest), ``regression`` (the new test) or ``base_files`` (the base) of an entry of
``SCENARIOS`` or by calling ``conftest_run`` directly: it lays that conftest in a real test commit
and runs the whole pipeline (static check, differential base probe, verifiers)."""

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import commit_change, git
from conftest_scenarios import (
    BASE_CONFTEST,
    FIXTURE,
    PLAIN,
    SCENARIOS,
    conftest_run,
)
from conftest_scenarios import project as _project  # noqa: F401  (the fixture, re-exposed below)
from test_deferred_tests import FIX, SeeingWorker, make_test_commit, run

import cairnwork.deferred_tests as dt
from cairnwork import p2p_conftest
from cairnwork.conftest_check import _own_packages, check_conftests, conftest_of, governed
from cairnwork_attest import binding_extras as bx

pytestmark = pytest.mark.sandbox


@pytest.fixture
def project(_project):  # noqa: F811
    return _project


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_a_conftest_test_commit_is_judged_by_what_it_does_to_the_base(project, tmp_path, name):
    kwargs, status, code = SCENARIOS[name]
    result = conftest_run(project, tmp_path, **kwargs)
    if table := os.environ.get("SCENARIO_TABLE"):  # the before/after table of the PR body
        row = {"name": name, "status": result.status, "reason": result.reason[:160]}
        Path(table).open("a").write(json.dumps(row) + "\n")
    assert result.status == status, result.reason
    assert code in result.reason


def test_a_fixture_only_conftest_ends_completed_with_the_conftest_in_the_tested_commit(
    project, tmp_path
):
    result = conftest_run(project, tmp_path)
    assert result.status == "COMPLETED", result.reason
    files = git(project, "diff", "--name-only", git(project, "rev-parse", "HEAD"),
                result.candidate_commit).split()  # fmt: skip
    assert sorted(files) == ["src/calc.py", "tests/conftest.py", "tests/test_regression.py"]
    assert result.p2p["configured"] == "whole-suite"


def test_the_edit_of_existing_conftest_code_is_refused(project, tmp_path):
    files = {"tests/conftest.py": "x = 1\n", "tests/test_regression.py": PLAIN}
    base, tests = make_test_commit(project, files)
    result = run(project, tmp_path, base, tests, SeeingWorker(FIX), p2p_mode="whole-suite")
    assert "TEST_COMMIT_CONFTEST_STATIC" in result.reason


def _commit(project, files, delete=()):
    base = git(project, "rev-parse", "HEAD")
    commit_change(project, files, delete=delete)
    commit = git(project, "rev-parse", "HEAD")
    git(project, "reset", "-q", "--hard", base)
    return base, commit


@pytest.mark.parametrize(
    ("addition", "ok"),
    [
        (FIXTURE, True),
        (FIXTURE.replace("fixture", "fixture(scope='session', params=[1, 2])"), True),
        ("\nimport os\nLIMIT = 3\n", True),
        ("\nimport requests\n", False),  # a third-party package
        ("\nfrom os import *\n", False),
        ("\nclass Box:\n    pass\n", False),
        ("\ndef helper():\n    return 1\n", False),  # not a fixture
        ("\nimport pytest\n\n\n@pytest.fixture\ndef test_x():\n    return 1\n", False),
        (
            "\nimport pytest\n\n\n@pytest.fixture(params=list(range(3)))\ndef x():\n    return 1\n",
            False,
        ),
        ("\nimport pytest as p\n\n\n@p.fixture\ndef x():\n    return 1\n", True),
        ("\nimport functools\n\n\n@functools.cache\ndef x():\n    return 1\n", False),
        ("\nLIMIT = len('x')\n", False),  # not a literal
        ("\ndef pytest_configure(config):\n    pass\n", False),
    ],
)
def test_the_static_check_allows_only_additive_fixtures_and_literals(project, addition, ok):
    base, commit = _commit(project, {"tests/conftest.py": BASE_CONFTEST + addition})
    if ok:
        check_conftests(project, base, commit, ["tests/conftest.py"])
    else:
        with pytest.raises(dt.TestCommitRejected, match="TEST_COMMIT_CONFTEST_STATIC"):
            check_conftests(project, base, commit, ["tests/conftest.py"])


@pytest.mark.parametrize(
    "addition",
    [
        "\nfrom pytest import fixture\nfixture = 1\n",
        "\nimport pytest\npytest = 1\n",
        "\nimport pytest\n\n\ndef pytest():\n    return 1\n",
    ],
)
def test_the_static_check_refuses_a_name_that_rebinds_an_import(project, addition):
    base, commit = _commit(project, {"tests/conftest.py": BASE_CONFTEST + addition})
    with pytest.raises(dt.TestCommitRejected, match="rebinds an import"):
        check_conftests(project, base, commit, ["tests/conftest.py"])


def test_a_repository_module_named_like_the_standard_library_is_a_repository_module(project):
    base, commit = _commit(project, {"src/random.py": "x = 1\n"})
    assert "random" in _own_packages(project, commit)
    base, commit = _commit(project, {"tests/conftest.py": BASE_CONFTEST + "\nimport random\n"})
    check_conftests(project, base, commit, ["tests/conftest.py"])  # a stdlib name is fine too


def test_a_conftest_at_the_repository_root_governs_every_base_test():
    assert governed(["conftest.py"], ["tests/a.py", "x/b.py"]) == ("tests/a.py", "x/b.py")


def test_helpers_pick_the_conftests_and_the_tests_under_their_directory():
    assert conftest_of(["tests/a/conftest.py", "tests/conftest.pyc", "tests/test_x.py"]) == (
        "tests/a/conftest.py",
    )
    assert governed(["tests/a/conftest.py"], ["tests/a/b/test_x.py", "tests/test_y.py"]) == (
        "tests/a/b/test_x.py",
    )
    two = ["tests/a/conftest.py", "tests/b/conftest.py"]  # #891: every conftest's directory
    assert governed(two, ["tests/a/test_x.py", "tests/b/test_y.py", "tests/test_z.py"]) == (
        "tests/a/test_x.py",
        "tests/b/test_y.py",
    )


def test_the_refusal_names_differing_tests_cleaned_and_bounded(monkeypatch):  # #891 nit
    odd, long = "tests/test_a.py::test_x[\x1b[31m]", "tests/test_b.py::test_" + "y" * 400
    monkeypatch.setattr(p2p_conftest, "lay_tests_over", lambda *a: "c" * 40)
    monkeypatch.setattr(p2p_conftest, "_side", lambda *a: ({odd: "failed", long: "failed"}, ""))
    task, side = (
        SimpleNamespace(repo=None, base_commit="", test_commit=""),
        SimpleNamespace(changed=set(), probe_only=set()),
    )
    result, ctx = SimpleNamespace(attempt_id="a"), SimpleNamespace(run_root="/nonexistent")
    with pytest.raises(dt.TestCommitRejected) as raised:
        p2p_conftest.check_overlay(task, ["tests/conftest.py"], (), {odd: "passed", long: "passed"},
                                   side, result, ctx, 1)  # fmt: skip
    detail = str(raised.value)
    assert "\x1b" not in detail and "test_x[?[31m]" in detail
    assert "y" * 200 not in detail and "y" * 150 in detail


def _ctx(binding, *, full, policy=None, repo=None):
    return SimpleNamespace(binding=binding, full=full, policy=policy, repo=repo)


def test_a_confidential_receiver_needs_allow_conftest_and_a_full_one_re_runs_the_static_check(
    project,
):
    base, commit = _commit(project, {"tests/conftest.py": BASE_CONFTEST + FIXTURE})
    binding = {"base": base, "test_commit": commit, "test_paths": ["tests/conftest.py"],
               "conftest_paths": ["tests/conftest.py"]}  # fmt: skip
    refused = bx.conftest_problem(_ctx(binding, full=False))
    assert refused is not None and refused.code == "CONFTEST_NOT_ALLOWED"
    assert bx.conftest_problem(_ctx(binding, full=False, policy={"allow_conftest": True})) is None
    assert bx.conftest_problem(_ctx(binding, full=True, repo=project)) is None
    bad, bad_commit = _commit(project, {"tests/conftest.py": "x = 1\n"})
    binding |= {"base": bad, "test_commit": bad_commit}
    failed = bx.conftest_problem(_ctx(binding, full=True, repo=project))
    assert failed is not None and failed.code == "CONFTEST_STATIC"


def test_the_binding_names_exactly_the_conftests_among_the_test_paths():
    both = ["tests/a/conftest.py", "tests/conftest.py"]
    ok = {"test_paths": both, "conftest_paths": both}
    assert bx.binding_problem(ok) == ""
    assert bx.binding_problem(ok | {"conftest_paths": both[::-1]})  # not sorted
    assert bx.binding_problem(ok | {"conftest_paths": ["tests/conftest.py"]})  # one left out
    assert bx.binding_problem({"test_paths": both})  # conftests among the paths, none named
    assert bx.binding_problem(ok | {"conftest_paths": ["tests/x/conftest.py"]})  # not a test path
    names = {"test_paths": ["tests/CONFTEST.py"], "conftest_paths": ["tests/CONFTEST.py"]}
    assert bx.binding_problem(names)  # not the exact basename
    assert bx.binding_problem({"test_paths": ["tests/test_x.py"]}) == ""
    assert bx.policy_problem({"allow_conftest": "yes"})
    assert not bx.policy_problem({"allow_conftest": True})


WRONG_TYPES = [[["tests/conftest.py"]], [1, "tests/conftest.py"], [["a"], "b/conftest.py"],
               [1, "b/conftest.py"], [None]]  # fmt: skip


@pytest.mark.parametrize("bad", WRONG_TYPES)
def test_a_conftest_list_of_the_wrong_type_is_a_shape_problem_not_an_exception(bad):
    binding = {"test_paths": ["tests/conftest.py"], "conftest_paths": bad}
    assert "conftest_paths" in bx.binding_problem(binding)
    assert "conftest_paths" in bx.policy_problem({"conftest_paths": bad})


@pytest.mark.parametrize("bad", [None, 3, [1], [None], [["x"]], "tests/conftest.py"])
def test_a_test_path_list_of_the_wrong_type_is_a_shape_problem_not_an_exception(bad):
    assert "test_paths" in bx.binding_problem({"test_paths": bad})


def test_a_base_test_named_by_the_python_files_rule_lets_the_probe_run(project, tmp_path):
    """#870 (11/06 re-verification, variant 1): the only base test is named by the base's
    python_files rule, not by a default name. The probe selects it as P2P does; on main the probe
    found no base test file and refused TEST_COMMIT_CONFTEST_UNCHECKED."""
    rule = {"pytest.ini": "[pytest]\npython_files = check_*.py\n"}
    unit = (
        "import sys, pathlib\nsys.path.insert(0, str(pathlib.Path(__file__).parents[2] / 'src'))\n"
    )
    base = {
        **rule,
        "tests/unit/check_calc.py": unit
        + "from calc import add\ndef test_base_add():\n    assert add(2, 3)\n",
    }
    result = conftest_run(project, tmp_path, path="tests/new/conftest.py", regression=PLAIN,
                          base_files=base, mode="module-dir")  # fmt: skip
    assert result.status == "COMPLETED", result.reason
    assert result.p2p["base_ran"] == 1 and result.p2p["targets"] == 1  # check_calc.py ran
