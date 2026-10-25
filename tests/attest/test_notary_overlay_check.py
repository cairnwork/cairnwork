"""Tests for Notary overlay verification before calling Core (#232)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from cairnwork.notary import Notary, NotaryInputError
from tests.attest.binding_helpers import (
    TESTS,
    executed,
    fake_core_verify,
    git,
    notarize_bound,
    producer,
)


def test_consistent_run_notarizes(tmp_path: Path) -> None:
    p = producer(tmp_path)
    state = tmp_path / "state"
    run = notarize_bound(state, p, unchecked=False)
    assert run["verdict"].verified
    conn = sqlite3.connect(state / "notary.sqlite")
    assert conn.execute("SELECT count(*) FROM core_verifications").fetchone()[0] == 1


def test_judged_commit_wrong_parent_raises(tmp_path: Path) -> None:
    p = producer(tmp_path)
    state = tmp_path / "state"
    tree, _ = executed(p["work"], p["head"], p["tests"], tuple(sorted(TESTS)))
    # Create commit with parent = base instead of head
    wrong_judged = git(p["work"], "commit-tree", tree, "-p", p["base"], "-m", "wrong parent")
    with pytest.raises(NotaryInputError) as exc_info:
        notarize_bound(state, p, tree_and_judged=(tree, wrong_judged), unchecked=False)
    assert "judged commit parent is not the binding head" in str(exc_info.value)

    # Database must have 0 rows recorded
    conn = sqlite3.connect(state / "notary.sqlite")
    assert conn.execute("SELECT count(*) FROM core_verifications").fetchone()[0] == 0


def test_executed_tree_mismatch_raises(tmp_path: Path) -> None:
    p = producer(tmp_path)
    state = tmp_path / "state"
    # Create a tree from head alone (not overlaid with tests)
    bad_tree = git(p["work"], "rev-parse", f"{p['head']}^{{tree}}")
    judged = git(p["work"], "commit-tree", bad_tree, "-p", p["head"], "-m", "bad tree")
    with pytest.raises(NotaryInputError) as exc_info:
        notarize_bound(state, p, tree_and_judged=(bad_tree, judged), unchecked=False)
    assert "binding executed_tree is not head overlaid with the test commit" in str(exc_info.value)

    conn = sqlite3.connect(state / "notary.sqlite")
    assert conn.execute("SELECT count(*) FROM core_verifications").fetchone()[0] == 0


def test_unchecked_path_bypasses_check(tmp_path: Path) -> None:
    p = producer(tmp_path)
    state = tmp_path / "state"
    tree, _ = executed(p["work"], p["head"], p["tests"], tuple(sorted(TESTS)))
    wrong_judged = git(p["work"], "commit-tree", tree, "-p", p["base"], "-m", "wrong parent")
    run = notarize_bound(state, p, tree_and_judged=(tree, wrong_judged), unchecked=True)
    assert run["verdict"] is not None


@pytest.mark.parametrize(
    "bad",
    [
        {"test_commit": None},
        {"test_commit": "0" * 39},
        {"test_paths": None},
        {"test_paths": "tests/test_a.py"},
        {"test_paths": ["tests/\x00a.py"]},
    ],
)
def test_malformed_binding_is_a_coded_input_error(tmp_path: Path, bad: dict) -> None:
    p = producer(tmp_path)
    tree, judged = executed(p["work"], p["head"], p["tests"], tuple(sorted(TESTS)))
    binding = {"head": p["head"], "test_commit": p["tests"], "test_paths": sorted(TESTS)}
    binding.update(bad)
    notary = Notary(tmp_path / "notary.sqlite", core_verify=fake_core_verify, core_identity={})
    with pytest.raises(NotaryInputError):
        notary._check_binding(p["work"], {**binding, "executed_tree": tree}, judged)
