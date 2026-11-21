"""Fail-closed cases for the external-decorator relaxation (review of PR for #1004)."""

import pytest
from test_conftest_names import _check

import cairnwork.deferred_tests as dt

NESTED = "tests/sub/conftest.py"
DEP = {NESTED: "import pytest\n\n\n@pytest.fixture\ndef dep():\n    return 1\n"}
FACTORY = "def register(fn):\n    return fn\n"


def _base(helper: str, extra: dict[str, str] | None = None) -> dict[str, str]:
    files = {
        "tests/conftest.py": "import sys, pathlib\n"
        "sys.path.insert(0, str(pathlib.Path(__file__).parent))\n"
        "pytest_plugins = ['helpers']\n",
        "tests/helpers.py": helper,
        "tests/factory.py": FACTORY,
    }
    return files | (extra or {})


BLOCKED = {
    "from_dot": "from . import factory\n\n\n@factory.register\ndef helper(dep):\n    return dep\n",
    "from_tests": "from tests import factory\n\n\n@factory.register\n"
    "def helper(dep):\n    return dep\n",
    "try_alias": "try:\n    from factory import register as deco\nexcept ImportError:\n"
    "    from functools import lru_cache as deco\n\n\n@deco\ndef helper(dep):\n    return dep\n",
    "nested_import": "from factory import register as cache\n\n\ndef other():\n"
    "    from functools import cache\n\n\n@cache\ndef helper(dep):\n    return dep\n",
    "local_def": "from functools import wraps\n\n\ndef wraps(fn):\n    return fn\n\n\n"
    "@wraps\ndef helper(dep):\n    return dep\n",
    "rebind": "import functools\nimport factory\n\nfunctools = factory\n\n\n"
    "@functools.register\ndef helper(dep):\n    return dep\n",
    "partial": "import functools\nimport factory\n\n\n@functools.partial(factory.register)\n"
    "def helper(dep):\n    return dep\n",
    "unresolved": "@missing_thing\ndef helper(dep):\n    return dep\n",
}


@pytest.mark.parametrize("helper", BLOCKED.values(), ids=BLOCKED.keys())
def test_bypass_forms_still_count_the_helper(repo, helper):
    with pytest.raises(dt.TestCommitRejected) as refused:
        _check(repo, DEP, path=NESTED, base_files=_base(helper))
    assert refused.value.code == "TEST_COMMIT_CONFTEST_STATIC"
    assert "dep" in str(refused.value)


SHADOW = {
    "tests/functools.py": "def lru_cache(fn):\n    return fn\n",
}
SHADOW_HELPER = "import functools\n\n\n@functools.lru_cache\ndef helper(dep):\n    return dep\n"


def test_in_tree_module_shadowing_stdlib_still_counts(repo):
    with pytest.raises(dt.TestCommitRejected):
        _check(repo, DEP, path=NESTED, base_files=_base(SHADOW_HELPER, SHADOW))


PASSING = {
    "stdlib": "import functools\n\n\n@functools.lru_cache\ndef helper(dep):\n    return dep\n",
    "stdlib_call": "import functools\n\n\n@functools.lru_cache(maxsize=1)\n"
    "def helper(dep):\n    return dep\n",
    "from_stdlib": "from functools import lru_cache\n\n\n@lru_cache\n"
    "def helper(dep):\n    return dep\n",
    "alias": "import functools as ft\n\n\n@ft.lru_cache\ndef helper(dep):\n    return dep\n",
    "contextlib": "from contextlib import contextmanager\n\n\n@contextmanager\n"
    "def helper(dep):\n    yield dep\n",
    "third_party": "import notinstalled_zzz\n\n\n@notinstalled_zzz.deco\n"
    "def helper(dep):\n    return dep\n",
}


@pytest.mark.parametrize("helper", PASSING.values(), ids=PASSING.keys())
def test_plain_external_decorators_do_not_count(repo, helper):
    _check(repo, DEP, path=NESTED, base_files=_base(helper))
