"""Malformed pyproject pytest tables must stop discovery instead of falling through."""

import pytest
from conftest import commit_change

from cairnwork.pytest_config import PytestConfigError, base_settings


@pytest.mark.parametrize(
    "pyproject",
    [
        '[tool]\npytest = "x"\n',
        '[tool.pytest]\nini_options = "x"\n',
    ],
)
def test_wrong_type_pyproject_pytest_table_is_refused(repo, pyproject):
    base = commit_change(
        repo,
        {
            "pyproject.toml": pyproject,
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
