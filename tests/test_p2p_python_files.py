"""#793: P2P picks base test files the way pytest's ``python_files`` matches them."""

import os
from types import SimpleNamespace
from typing import Any

import pytest
from conftest import commit_change
from test_p2p import FIX_BREAKS_NEG, SIBLING, configured_project, run

from cairnwork.p2p import P2PRejected, _base_tests
from cairnwork.p2p_selection import select_p2p_targets
from cairnwork.pytest_config import _file_matches
from cairnwork.pytest_config import test_file_rule as base_file_rule


@pytest.mark.parametrize(
    ("pattern", "path", "expected"),
    [
        ("check_*.py", "tests/a/check_x.py", True),  # no "/": the file name only
        ("check_*.py", "tests/check/x.py", False),
        ("tests/*.py", "tests/test_x.py", True),  # "/": the whole path, as */tests/*.py
        ("tests/*.py", "pkg/tests/deep/x.py", True),  # "*" crosses "/" as in fnmatch
        ("tests/*.py", "src/x.py", False),
        ("*/test_*.py", "test_top.py", True),  # pytest matches the absolute path: "/" leads
    ],
)
def test_a_pattern_matches_as_pytest_matches_it(pattern, path, expected):
    assert _file_matches(pattern, path) is expected


@pytest.mark.parametrize(
    ("ini", "path", "expected"),
    [
        ("[pytest]\npython_files = /home/*/test_*.py\n", "tests/test_x.py", True),  # default added
        ("[pytest]\npython_files = */*/*/*/test_*.py\n", "tests/test_x.py", True),
        ("[pytest]\npython_files = check_*.py\n", "tests/test_x.py", False),  # no "/": no default
        # mixed: one pattern has "/", so the default is added (kills the any -> all mutant)
        ("[pytest]\npython_files = check_*.py lib/*.py\n", "tests/test_x.py", True),
        (
            '[tool.pytest.ini_options]\npython_files = ["check_*.py spec_*.py"]\n',
            "t/spec_x.py",
            True,
        ),
    ],
)
def test_the_base_file_rule_never_selects_fewer_than_the_default(repo, ini, path, expected):
    name = "pyproject.toml" if ini.startswith("[tool") else "pytest.ini"
    base = commit_change(repo, {name: ini})
    assert base_file_rule(repo, base)(path) is expected


@pytest.mark.sandbox
@pytest.mark.parametrize("pattern", ["tests/*.py", "/home/*/test_*.py", "*/*/*/*/test_*.py"])
def test_a_regression_under_a_directory_pattern_is_caught(repo, tmp_path, pattern):
    project = configured_project(repo, f"[pytest]\npython_files = {pattern}\n")
    commit_change(project, {"tests/test_sibling.py": SIBLING})
    result = run(project, tmp_path, {"src/calc.py": FIX_BREAKS_NEG}, mode="whole-suite")
    assert result.status == "FAILED" and not result.verified
    assert "tests/test_sibling.py::test_neg" in result.p2p["rerun"]["failed"]


def test_an_unreadable_base_config_refuses_the_base_test_listing(repo):
    os.symlink("elsewhere.ini", repo / "pytest.ini")  # not a regular file at the base
    base = commit_change(repo, {"tests/test_x.py": SIBLING})
    task: Any = SimpleNamespace(repo=repo, base_commit=base, test_roots=("tests",))
    with pytest.raises(P2PRejected) as got:
        _base_tests(task, frozenset())
    assert got.value.code == "P2P_BASE_UNREADABLE" and "not a regular file" in str(got.value)


# #870: the name match of module-dir follows the base's python_files too
NESTED = ("tests/sub/check_calc.py", "tests/sub/check_other.py")


def test_a_custom_python_files_name_selects_the_test_of_a_changed_file(repo):
    ini = "[pytest]\npython_files = check_*.py\n"
    base = commit_change(repo, {"pytest.ini": ini, **{p: SIBLING for p in NESTED}})
    changed, roots = ("src/calc.py",), ("tests",)
    # what main does (no rule): check_calc.py is not "test_calc.py", nothing is picked
    assert select_p2p_targets(changed, NESTED, roots, "module-dir") == ()
    rule = base_file_rule(repo, base)
    assert select_p2p_targets(changed, NESTED, roots, "module-dir", rule) == NESTED


def test_without_a_python_files_setting_the_default_names_apply(repo):
    base = commit_change(repo, {"pytest.ini": "[pytest]\n", **{p: SIBLING for p in NESTED}})
    rule = base_file_rule(repo, base)
    assert select_p2p_targets(("src/calc.py",), NESTED, ("tests",), "module-dir", rule) == ()
    suite = ("tests/sub/test_calc.py", "tests/sub/test_other.py")
    out = select_p2p_targets(("src/calc.py",), suite, ("tests",), "module-dir", rule)
    assert out == suite
