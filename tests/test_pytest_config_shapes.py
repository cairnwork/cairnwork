"""#928: the base's python_files as pytest 9 reads them. For each config shape the deep audit
found, the files pytest itself collects on the same base are checked against Cairnwork's rule
(never fewer), or the base is refused (PYTEST_CONFIG_ERROR). The pipeline tests are the
before/after rows of the PR: a candidate that breaks a base test is not VERIFIED."""

import subprocess
import sys

import pytest
from conftest import commit_change, git
from test_p2p import FIX_BREAKS_NEG, SIBLING, configured_project, run

from cairnwork.pipeline import TaskRejected
from cairnwork.pytest_config import PytestConfigError, base_settings
from cairnwork.pytest_config import test_file_rule as base_file_rule
from cairnwork.pytest_config_files import _section

PLAIN = "def test_x():\n    assert True\n"
FILES = ("tests/test_core.py", "tests/check_x.py", "tests/it's_x.py", "tests/a b1.py",
         "tests/b1.py")  # fmt: skip


def _base(repo, config):
    return commit_change(repo, {**config, **{p: PLAIN for p in FILES}})


def _pytest_collects(repo, base, tmp_path):
    """The test files pytest itself collects on a checkout of ``base`` (pytest 9)."""
    tree = tmp_path / "checkout"
    git(repo, "worktree", "add", "-q", "--detach", str(tree), base)
    done = subprocess.run(  # noqa: S603 - this interpreter, a fixed argv
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider",
         "tests"],
        cwd=tree, capture_output=True, text=True, timeout=120,
    )  # fmt: skip
    return {line.split("::")[0] for line in done.stdout.splitlines() if "::" in line}


NATIVE = '[tool.pytest]\npython_files = ["check_*.py"]\n'
SAME_OR_MORE = {  # shape: (config files, what pytest collects there)
    "an ini file with a UTF-8 BOM": (
        {"pytest.ini": "\ufeff[pytest]\npython_files = check_*.py\n"},
        {"tests/check_x.py"},
    ),
    "[DEFAULT] is not merged into [pytest]": (
        {"pytest.ini": "[DEFAULT]\npython_files = check_*.py\n[pytest]\n"},
        {"tests/test_core.py"},
    ),
    "an empty [tool.pytest] is not a config": (
        {"pyproject.toml": "[tool.pytest]\n", "tox.ini": "[pytest]\npython_files = check_*.py\n"},
        {"tests/check_x.py"},
    ),
    "native keys win over an empty ini_options table": (
        {"pyproject.toml": NATIVE + "[tool.pytest.ini_options]\n"},
        {"tests/check_x.py"},
    ),
    "a list item with a quote is a pattern": (
        {"pyproject.toml": '[tool.pytest.ini_options]\npython_files = ["it\'s_*.py"]\n'},
        {"tests/it's_x.py"},
    ),
    "a list item with a space is a pattern": (
        {"pyproject.toml": '[tool.pytest.ini_options]\npython_files = ["a b*.py"]\n'},
        {"tests/a b1.py"},
    ),
}


@pytest.mark.parametrize("shape", list(SAME_OR_MORE))
def test_the_rule_selects_every_file_pytest_collects(repo, tmp_path, shape):
    config, expected = SAME_OR_MORE[shape]
    base = _base(repo, config)
    collected = _pytest_collects(repo, base, tmp_path)
    assert collected == expected  # the shape does what the audit saw pytest 9 do
    rule = base_file_rule(repo, base)
    assert all(rule(p) for p in collected)


def test_ini_section_strips_one_leading_utf8_bom():
    assert _section("\ufeff[pytest]\naddopts = -q\n", "pytest", "ini", "pytest.ini") == {
        "addopts": "-q"
    }


def test_toml_section_with_a_bom_is_still_refused():
    with pytest.raises(PytestConfigError, match="cannot be parsed"):
        _section("\ufeff[pytest]\n", "pytest", "toml", "pytest.toml")


REFUSED = {
    "a pytest.toml next to a pytest.ini": {
        "pytest.ini": "[pytest]\npython_files = check_*.py\n",
        "pytest.toml": '[pytest]\npython_files = ["test_*.py"]\n',
    },
    "a .pytest.toml next to a .pytest.ini": {".pytest.ini": "[pytest]\n", ".pytest.toml": ""},
    "[tool.pytest] and [tool.pytest.ini_options] together": {
        "pyproject.toml": '[tool.pytest]\nminversion = "9"\n[tool.pytest.ini_options]\nx = "1"\n',
    },
}


@pytest.mark.parametrize("shape", list(REFUSED))
def test_a_base_whose_config_pytest_versions_read_differently_is_refused(repo, shape):
    base = _base(repo, REFUSED[shape])
    with pytest.raises(PytestConfigError):
        base_settings(repo, base)


def test_a_python_files_string_shlex_cannot_split_refuses_the_base(repo):
    base = _base(repo, {"pytest.ini": "[pytest]\npython_files = it's_*.py\n"})
    with pytest.raises(PytestConfigError, match="python_files"):
        base_file_rule(repo, base)


def test_shapes_pytest_reads_the_same_way_stay_found(repo):
    """Controls: only the shapes above change; an ini-mode table, a native one, one file alone."""
    for config, source in (
        ({"pyproject.toml": "[tool.pytest.ini_options]\n"}, "pyproject.toml"),
        ({"pyproject.toml": '[tool.pytest]\nminversion = "9"\n'}, "pyproject.toml"),
        ({"pytest.toml": "", "tox.ini": "[pytest]\n"}, "pytest.toml"),
        ({"pytest.ini": "[pytest]\n[DEFAULT]\nx = 1\n", "setup.cfg": "[tool:pytest]\n"},
         "pytest.ini"),
    ):  # fmt: skip
        assert base_settings(repo, commit_change(repo, config)).source == source
        for name in config:
            git(repo, "rm", "-q", name)
        git(repo, "commit", "-qm", "reset")


@pytest.mark.sandbox
def test_a_default_section_no_longer_hides_a_regression(repo, tmp_path):
    """Before/after row 1: on main the [DEFAULT] key made python_files check_*.py, P2P bound no
    base test and the candidate that breaks neg() was VERIFIED."""
    project = configured_project(repo, "[DEFAULT]\npython_files = check_*.py\n[pytest]\n")
    commit_change(project, {"tests/test_sibling.py": SIBLING})
    result = run(project, tmp_path, {"src/calc.py": FIX_BREAKS_NEG}, mode="whole-suite")
    assert result.status == "FAILED" and not result.verified
    assert "tests/test_sibling.py::test_neg" in result.p2p["rerun"]["failed"]


@pytest.mark.sandbox
def test_a_pytest_toml_beside_a_pytest_ini_is_refused_not_verified(repo, tmp_path):
    """Before/after row 2: on main Cairnwork read pytest.ini (check_*.py), pytest 9 reads
    pytest.toml; the candidate that breaks neg() was VERIFIED."""
    project = configured_project(repo, "[pytest]\npython_files = check_*.py\n")
    toml = '[pytest]\npython_files = ["test_*.py"]\n'
    commit_change(project, {"pytest.toml": toml, "tests/test_sibling.py": SIBLING})
    with pytest.raises(TaskRejected, match="a pytest.toml and a pytest.ini"):  # before binding
        run(project, tmp_path, {"src/calc.py": FIX_BREAKS_NEG}, mode="whole-suite")
