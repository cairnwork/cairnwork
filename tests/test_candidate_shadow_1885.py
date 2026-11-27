"""Tests for Issue #1885: iniconfig and packaging belong to the always-shadow list.

iniconfig is preloaded before candidate code runs, but a candidate module of the same name must
still be refused as a shadow (defence in depth). packaging may be loaded late, so the same
refusal is required there.
"""

from __future__ import annotations

import pytest
from conftest import commit_change

from cairnwork import candidate_shadow


@pytest.mark.parametrize("name", ["iniconfig", "packaging"])
def test_added_module_named_like_a_pytest_dependency_is_a_shadow(repo, name):
    cand = commit_change(repo, {f"sub/{name}.py": "X = 1\n"})
    assert (
        candidate_shadow.find_shadow_file(repo, cand, changed={f"sub/{name}.py": "ADD"})
        == f"sub/{name}.py"
    )


@pytest.mark.parametrize("name", ["iniconfig", "packaging"])
def test_added_package_named_like_a_pytest_dependency_is_a_shadow(repo, name):
    path = f"sub/{name}/__init__.py"
    cand = commit_change(repo, {path: "X = 1\n"})
    assert candidate_shadow.find_shadow_file(repo, cand, changed={path: "ADD"}) == path


def test_unrelated_name_is_not_a_shadow(repo):
    cand = commit_change(repo, {"sub/unrelatedhelper.py": "X = 1\n"})
    assert (
        candidate_shadow.find_shadow_file(repo, cand, changed={"sub/unrelatedhelper.py": "ADD"})
        is None
    )
