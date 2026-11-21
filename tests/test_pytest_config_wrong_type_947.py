"""Malformed pyproject pytest tables must stop discovery instead of falling through."""

import pytest
from conftest import commit_change

from cairnwork.pytest_config import PytestConfigError, base_settings


@pytest.mark.parametrize("wrong_type", ['"x"', "3", "true", "[]"])
@pytest.mark.parametrize(
    "pyproject",
    [
        "tool = {wrong_type}\n",
        "[tool]\npytest = {wrong_type}\n",
        "[tool.pytest]\nini_options = {wrong_type}\n",
    ],
    ids=["tool", "tool.pytest", "tool.pytest.ini_options"],
)
def test_wrong_type_pyproject_table_is_refused(repo, pyproject, wrong_type):
    base = commit_change(
        repo,
        {
            "pyproject.toml": pyproject.format(wrong_type=wrong_type),
            "tox.ini": "[pytest]\npython_files = test_*.py\n",
        },
    )

    with pytest.raises(PytestConfigError, match="PYTEST_CONFIG_ERROR"):
        base_settings(repo, base)


@pytest.mark.parametrize("wrong_type", ['"x"', "3", "true", "[]"])
@pytest.mark.parametrize("filename", ["pytest.toml", ".pytest.toml"])
def test_wrong_type_standalone_pytest_table_is_refused(repo, filename, wrong_type):
    base = commit_change(
        repo,
        {
            filename: f"pytest = {wrong_type}\n",
            "tox.ini": "[pytest]\npython_files = test_*.py\n",
        },
    )

    with pytest.raises(PytestConfigError, match="PYTEST_CONFIG_ERROR"):
        base_settings(repo, base)


def test_absent_pytest_table_still_allows_tox_fallback(repo):
    base = commit_change(
        repo,
        {
            "pyproject.toml": '[project]\nname = "sample"\n',
            "tox.ini": "[pytest]\npython_files = test_*.py\n",
        },
    )

    assert base_settings(repo, base).source == "tox.ini"
