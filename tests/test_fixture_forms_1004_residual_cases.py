"""Regression cases for fixture-name discovery in Issue #1004."""

import pytest
from test_conftest_names import _check

import cairnwork.deferred_tests as dt

ABOVE = "tests/conftest.py"
NESTED = "tests/sub/conftest.py"


def _base(helper: str) -> dict[str, str]:
    return {
        ABOVE: "import sys, pathlib\n"
        "sys.path.insert(0, str(pathlib.Path(__file__).parent))\n"
        "pytest_plugins = ['helpers']\n",
        "tests/helpers.py": helper,
    }


def test_external_decorator_does_not_make_helper_a_fixture_factory(repo):
    _check(
        repo,
        {NESTED: "import pytest\n\n\n@pytest.fixture\ndef helper():\n    return 1\n"},
        path=NESTED,
        base_files=_base(
            "import functools\n\n\n@functools.lru_cache\n"
            "def helper(monkeypatch):\n    return monkeypatch\n"
        ),
    )


def test_in_tree_factory_decorator_still_counts_as_fixture(repo):
    helper = "import factory\n\n\n@factory.register\ndef helper(dep):\n    return dep\n"
    with pytest.raises(dt.TestCommitRejected) as refused:
        _check(
            repo,
            {NESTED: "import pytest\n\n\n@pytest.fixture\ndef dep():\n    return 1\n"},
            path=NESTED,
            base_files={**_base(helper), "tests/factory.py": "def register(fn):\n    return fn\n"},
        )
    assert refused.value.code == "TEST_COMMIT_CONFTEST_STATIC"
    assert "dep" in str(refused.value)


def test_forwarded_literal_fixture_name_is_registered(repo):
    helper = (
        "def fixture(**kwargs):\n    return lambda fn: fn\n\n\n"
        "def wrap(n):\n    return fixture(name=n)\n\n\n"
        "@wrap('zz')\ndef helper():\n    return 1\n"
    )
    with pytest.raises(dt.TestCommitRejected) as refused:
        _check(
            repo,
            {NESTED: "import pytest\n\n\n@pytest.fixture(name='zz')\ndef fresh():\n    return 1\n"},
            path=NESTED,
            base_files=_base(helper),
        )
    assert refused.value.code == "TEST_COMMIT_CONFTEST_STATIC"
    assert "zz" in str(refused.value)
