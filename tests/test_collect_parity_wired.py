"""#1768 AC3 at the P2P base walk: strict refuses a nested naming config; the env check does not."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from conftest import commit_change

from cairnwork.deferred_tests import BaseUncomparable
from cairnwork.p2p import _base_tests

TEST = "def test_a(): pass\n"
NESTED = {"sub/pytest.ini": "[pytest]\npython_files = check_*\n"}


def _task(repo, files):
    base = commit_change(repo, {"tests/test_a.py": TEST, **files})
    return SimpleNamespace(
        repo=repo, base_commit=base, test_roots=("tests",), allowed_files=(), test_commit=""
    )


def test_strict_walk_refuses_a_nested_naming_config(repo):
    with pytest.raises(BaseUncomparable) as got:
        _base_tests(_task(repo, NESTED), frozenset())
    assert got.value.comparison.code == "P2P_BASE_NESTED_NAMING"


def test_the_env_check_walk_is_not_refused(repo):
    assert _base_tests(_task(repo, NESTED), frozenset(), strict=False) == ("tests/test_a.py",)


def test_a_plain_repo_walks_as_before(repo):
    assert _base_tests(_task(repo, {}), frozenset()) == ("tests/test_a.py",)
