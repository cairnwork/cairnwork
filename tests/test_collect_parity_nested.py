"""#1768 AC3: a pytest config below the root that sets the test-naming keys is refused.

The verifier's pinned ini never reads it, so a root-run comparison cannot see it; the base is
UNCOMPARABLE ``P2P_BASE_NESTED_NAMING`` instead. Controls: other keys, a root config, no config.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from conftest import commit_change

from cairnwork.collect_parity import nested_naming
from cairnwork.deferred_tests import BaseUncomparable

NAMING = ("python_files", "python_classes", "python_functions")
INI = "[pytest]\n{key} = {value}\n"
FORMS = {
    "sub/pytest.ini": INI,
    "sub/.pytest.ini": INI,
    "sub/tox.ini": INI,
    "sub/setup.cfg": "[tool:pytest]\n{key} = {value}\n",
    "sub/pytest.toml": '[pytest]\n{key} = "{value}"\n',
    "sub/pyproject.toml": '[tool.pytest.ini_options]\n{key} = "{value}"\n',
    "a/b/c/pytest.ini": INI,
}


def _task(repo, files):
    base = commit_change(repo, {"tests/test_a.py": "def test_a(): pass\n", **files})
    return SimpleNamespace(repo=repo, base_commit=base, test_roots=("tests",))


@pytest.mark.parametrize("key", NAMING)
@pytest.mark.parametrize("name", sorted(FORMS))
def test_a_nested_config_that_sets_a_naming_key_is_refused(repo, name, key):
    task = _task(repo, {name: FORMS[name].format(key=key, value="check_*")})
    with pytest.raises(BaseUncomparable) as got:
        nested_naming(task)
    assert got.value.comparison.code == "P2P_BASE_NESTED_NAMING"


@pytest.mark.parametrize("name", sorted(FORMS))
def test_a_nested_config_with_other_keys_is_not_refused(repo, name):
    nested_naming(_task(repo, {name: FORMS[name].format(key="markers", value="slow")}))


@pytest.mark.parametrize("name", ["pytest.ini", "tox.ini", "setup.cfg", "pyproject.toml"])
def test_a_root_config_is_not_this_rule(repo, name):
    text = FORMS["sub/" + name].format(key="python_files", value="check_*")
    nested_naming(_task(repo, {name: text}))


def test_a_plain_repo_is_not_refused(repo):
    nested_naming(_task(repo, {}))


def test_an_unreadable_nested_config_is_refused_never_passed(repo):
    task = _task(repo, {"sub/pytest.toml": "[pytest\nnot toml"})
    with pytest.raises(BaseUncomparable) as got:
        nested_naming(task)
    assert got.value.comparison.code == "P2P_BASE_NESTED_NAMING"


def test_a_file_that_only_looks_like_a_config_is_not_one(repo):
    nested_naming(_task(repo, {"sub/not_pytest.ini": INI.format(key="python_files", value="x")}))
